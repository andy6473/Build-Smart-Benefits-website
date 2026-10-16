from __future__ import annotations

import calendar
import hmac
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager, closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Annotated, AsyncIterator
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent
EASTERN = ZoneInfo("America/New_York")
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))
SECURITY = HTTPBasic()
COVERAGE_CATEGORIES = ("Investment Strategy", "Gold Mine Strategy")
AGENT_STATUSES = ("Licensed Agent", "Unlicensed Agent", "Not an Agent")
BOOKING_TIMES = tuple(time(hour).strftime("%H:%M") for hour in range(9, 18))
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_UPLOAD_TYPES = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS contact_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL,
    coverage_group TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webinar_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT NOT NULL,
    agent_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consultation_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT NOT NULL,
    appointment_date TEXT NOT NULL,
    appointment_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (appointment_date, appointment_time)
);
CREATE TABLE IF NOT EXISTS job_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT NOT NULL,
    address TEXT NOT NULL,
    license_number TEXT NOT NULL DEFAULT '',
    license_file TEXT NOT NULL DEFAULT '',
    resume_file TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _initialize_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(database_path)) as connection:
        connection.executescript(SCHEMA)
        connection.commit()


def _csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def _check_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Form expired. Reload the page and try again.")


def _clean(value: str, field: str, max_length: int) -> str:
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise HTTPException(status_code=422, detail=f"{field} must be at most {max_length} characters.")
    return cleaned


def _validate_email(value: str) -> str:
    email = _clean(value, "Email", 254)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return email


def _validate_required(value: str, field: str, max_length: int = 200) -> str:
    cleaned = _clean(value, field, max_length)
    if not cleaned:
        raise HTTPException(status_code=422, detail=f"{field} is required.")
    return cleaned


def _valid_appointment_day(day: date) -> bool:
    today = datetime.now(EASTERN).date()
    return today <= day <= today + timedelta(days=180) and day.weekday() != 6


def _available_times(database_path: Path, appointment_day: date) -> list[str]:
    if not _valid_appointment_day(appointment_day):
        return []
    with closing(_connect(database_path)) as connection:
        rows = connection.execute(
            "SELECT appointment_time FROM consultation_bookings WHERE appointment_date = ?",
            (appointment_day.isoformat(),),
        ).fetchall()
    booked = {row["appointment_time"] for row in rows}
    available = [slot for slot in BOOKING_TIMES if slot not in booked]
    if appointment_day == datetime.now(EASTERN).date():
        now = datetime.now(EASTERN).time().replace(tzinfo=None)
        available = [
            slot
            for slot in available
            if datetime.strptime(slot, "%H:%M").time() > now
        ]
    return available


def _calendar_month(value: str | None) -> date:
    today = datetime.now(EASTERN).date()
    if value:
        try:
            month = datetime.strptime(value, "%Y-%m").date().replace(day=1)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Choose a valid calendar month.") from error
        if (month.year, month.month) < (today.year, today.month):
            return today.replace(day=1)
        if month > (today + timedelta(days=180)).replace(day=1):
            return (today + timedelta(days=180)).replace(day=1)
        return month
    return today.replace(day=1)


def _calendar_data(month: date) -> list[list[dict[str, object]]]:
    weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(month.year, month.month)
    today = datetime.now(EASTERN).date()
    latest = today + timedelta(days=180)
    return [
        [
            {
                "date": day,
                "in_month": day.month == month.month,
                "available": day.month == month.month and today <= day <= latest and day.weekday() != 6,
            }
            for day in week
        ]
        for week in weeks
    ]


def _format_time(slot: str) -> str:
    hour, minute = map(int, slot.split(":"))
    period = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {period} ET"


def _render(request: Request, template: str, **context: object) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name=template,
        context={
            "csrf_token": _csrf_token(request),
            "current_year": datetime.now(EASTERN).year,
            **context,
        },
    )


def _admin_credentials(
    credentials: Annotated[HTTPBasicCredentials, Depends(SECURITY)],
) -> str:
    expected_user = os.getenv("ADMIN_USERNAME", "admin")
    expected_password = os.getenv("ADMIN_PASSWORD", "")
    if not expected_password:
        raise HTTPException(
            status_code=503,
            detail="Admin access is disabled. Configure ADMIN_PASSWORD to enable it.",
        )
    valid_user = hmac.compare_digest(credentials.username, expected_user)
    valid_password = hmac.compare_digest(credentials.password, expected_password)
    if not (valid_user and valid_password):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def create_app(
    database_path: Path | str | None = None,
    uploads_path: Path | str | None = None,
) -> FastAPI:
    production = os.getenv("APP_ENV") == "production"
    session_secret = os.getenv("SESSION_SECRET")
    if production and not session_secret:
        raise RuntimeError("SESSION_SECRET must be configured when APP_ENV=production.")
    data_dir = Path(os.getenv("DATA_DIR", ROOT.parent / "data"))
    selected_database_path = (
        Path(database_path) if database_path else data_dir / "smart_benefits.sqlite3"
    )
    selected_uploads_path = Path(uploads_path) if uploads_path else data_dir / "uploads"

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        _initialize_database(selected_database_path)
        yield

    app = FastAPI(
        title="Smart Benefits",
        description="Business, personal, and family insurance guidance.",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.database_path = selected_database_path
    app.state.uploads_path = selected_uploads_path
    app.state.uploads_path.mkdir(parents=True, exist_ok=True)
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret or secrets.token_urlsafe(48),
        same_site="lax",
        https_only=production,
    )
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return _render(request, "home.html", title="Smart Benefits — Insurance & Financial Guidance")

    @app.get("/about", response_class=HTMLResponse)
    def about(request: Request) -> HTMLResponse:
        return _render(request, "about.html", title="About Smart Benefits")

    @app.get("/compare", response_class=HTMLResponse)
    def compare(request: Request) -> HTMLResponse:
        return _render(request, "compare.html", title="Compare Coverage Areas")

    @app.get("/contact", response_class=HTMLResponse)
    def contact(request: Request) -> HTMLResponse:
        return _render(request, "contact.html", title="Contact Smart Benefits")

    @app.post("/contact")
    def submit_contact(
        request: Request,
        csrf_token: Annotated[str, Form()],
        name: Annotated[str, Form()],
        email: Annotated[str, Form()],
        coverage_group: Annotated[str, Form()],
        phone: Annotated[str, Form()] = "",
        message: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        _check_csrf(request, csrf_token)
        name = _validate_required(name, "Name")
        email = _validate_email(email)
        phone = _clean(phone, "Phone", 40)
        message = _clean(message, "Message", 4000)
        if coverage_group not in COVERAGE_CATEGORIES:
            raise HTTPException(status_code=422, detail="Choose a valid coverage category.")
        with closing(_connect(request.app.state.database_path)) as connection:
            connection.execute(
                """INSERT INTO contact_messages
                (name, phone, email, coverage_group, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (name, phone, email, coverage_group, message, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            connection.commit()
        return RedirectResponse("/contact?sent=1", status_code=303)

    @app.get("/rsvp", response_class=HTMLResponse)
    def rsvp(request: Request) -> HTMLResponse:
        return _render(request, "rsvp.html", title="Saturday Webinar RSVP")

    @app.post("/rsvp")
    def submit_rsvp(
        request: Request,
        csrf_token: Annotated[str, Form()],
        name: Annotated[str, Form()],
        phone: Annotated[str, Form()],
        email: Annotated[str, Form()],
        agent_status: Annotated[str, Form()],
    ) -> RedirectResponse:
        _check_csrf(request, csrf_token)
        name = _validate_required(name, "Name")
        phone = _validate_required(phone, "Phone", 40)
        email = _validate_email(email)
        if agent_status not in AGENT_STATUSES:
            raise HTTPException(status_code=422, detail="Choose a valid agent status.")
        with closing(_connect(request.app.state.database_path)) as connection:
            connection.execute(
                """INSERT INTO webinar_registrations
                (name, phone, email, agent_status, created_at) VALUES (?, ?, ?, ?, ?)""",
                (name, phone, email, agent_status, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            connection.commit()
        return RedirectResponse("/rsvp?registered=1", status_code=303)

    @app.get("/book", response_class=HTMLResponse)
    def book(
        request: Request,
        selected_date: str | None = Query(default=None, alias="date"),
        month: str | None = None,
        booked: bool = False,
    ) -> HTMLResponse:
        calendar_month = _calendar_month(month)
        chosen_day: date | None = None
        slots: list[str] = []
        if selected_date:
            try:
                candidate = date.fromisoformat(selected_date)
            except ValueError:
                raise HTTPException(status_code=422, detail="Choose a valid appointment date.")
            if not _valid_appointment_day(candidate) or (
                candidate.year,
                candidate.month,
            ) != (calendar_month.year, calendar_month.month):
                raise HTTPException(status_code=422, detail="Choose an available appointment date.")
            chosen_day = candidate
            slots = _available_times(request.app.state.database_path, candidate)
        previous_month = (calendar_month - timedelta(days=1)).replace(day=1)
        next_month = (calendar_month + timedelta(days=32)).replace(day=1)
        current_month = datetime.now(EASTERN).date().replace(day=1)
        last_month = (datetime.now(EASTERN).date() + timedelta(days=180)).replace(day=1)
        return _render(
            request,
            "book.html",
            title="Book a Free Consultation",
            month=calendar_month,
            weeks=_calendar_data(calendar_month),
            chosen_day=chosen_day,
            available_times=slots,
            formatted_times={slot: _format_time(slot) for slot in slots},
            previous_month=previous_month.strftime("%Y-%m"),
            next_month=next_month.strftime("%Y-%m"),
            can_previous_month=calendar_month > current_month,
            can_next_month=calendar_month < last_month,
            booking_complete=booked,
        )

    @app.post("/book")
    def submit_booking(
        request: Request,
        csrf_token: Annotated[str, Form()],
        name: Annotated[str, Form()],
        phone: Annotated[str, Form()],
        email: Annotated[str, Form()],
        appointment_date: Annotated[str, Form()],
        appointment_time: Annotated[str, Form()],
    ) -> RedirectResponse:
        _check_csrf(request, csrf_token)
        name = _validate_required(name, "Name")
        phone = _validate_required(phone, "Phone", 40)
        email = _validate_email(email)
        try:
            day = date.fromisoformat(appointment_date)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Choose a valid appointment date.") from error
        if not _valid_appointment_day(day) or appointment_time not in BOOKING_TIMES:
            raise HTTPException(status_code=422, detail="Choose an available consultation time.")
        if appointment_time not in _available_times(request.app.state.database_path, day):
            raise HTTPException(status_code=409, detail="That time was just booked. Please choose another.")
        with closing(_connect(request.app.state.database_path)) as connection:
            try:
                connection.execute(
                    """INSERT INTO consultation_bookings
                    (name, phone, email, appointment_date, appointment_time, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        name,
                        phone,
                        email,
                        day.isoformat(),
                        appointment_time,
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                raise HTTPException(
                    status_code=409,
                    detail="That time was just booked. Please choose another.",
                ) from error
        return RedirectResponse("/book?booked=1", status_code=303)

    @app.get("/careers", response_class=HTMLResponse)
    def careers(request: Request) -> HTMLResponse:
        return _render(request, "careers.html", title="Careers at Smart Benefits")

    @app.post("/careers")
    async def submit_application(
        request: Request,
        csrf_token: Annotated[str, Form()],
        name: Annotated[str, Form()],
        phone: Annotated[str, Form()],
        email: Annotated[str, Form()],
        address: Annotated[str, Form()],
        license_number: Annotated[str, Form()] = "",
        license_file: Annotated[UploadFile | None, File()] = None,
        resume_file: Annotated[UploadFile | None, File()] = None,
    ) -> RedirectResponse:
        _check_csrf(request, csrf_token)
        name = _validate_required(name, "Name")
        phone = _validate_required(phone, "Phone", 40)
        email = _validate_email(email)
        address = _validate_required(address, "Address", 500)
        license_number = _clean(license_number, "License number", 100)
        stored: list[Path] = []
        saved_files: dict[str, str] = {}
        try:
            for field, upload in (("license", license_file), ("resume", resume_file)):
                if upload is None or not upload.filename:
                    saved_files[field] = ""
                    continue
                extension = Path(upload.filename).suffix.lower()
                accepted_mimes = ALLOWED_UPLOAD_TYPES.get(extension)
                if accepted_mimes is None or upload.content_type not in accepted_mimes:
                    raise HTTPException(status_code=415, detail=f"{field.title()} must be a PDF, DOC, or DOCX file.")
                contents = await upload.read(MAX_UPLOAD_BYTES + 1)
                if len(contents) > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"{field.title()} exceeds the 10 MB upload limit.")
                filename = f"{uuid.uuid4().hex}{extension}"
                destination = request.app.state.uploads_path / filename
                destination.write_bytes(contents)
                stored.append(destination)
                saved_files[field] = filename
        except Exception:
            for path in stored:
                path.unlink(missing_ok=True)
            raise
        with closing(_connect(request.app.state.database_path)) as connection:
            try:
                connection.execute(
                    """INSERT INTO job_applications
                    (name, phone, email, address, license_number, license_file, resume_file, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        name,
                        phone,
                        email,
                        address,
                        license_number,
                        saved_files["license"],
                        saved_files["resume"],
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )
                connection.commit()
            except Exception:
                for path in stored:
                    path.unlink(missing_ok=True)
                raise
        return RedirectResponse("/careers?submitted=1", status_code=303)

    @app.get("/admin", response_class=HTMLResponse)
    def admin(
        request: Request,
        _username: Annotated[str, Depends(_admin_credentials)],
    ) -> HTMLResponse:
        with closing(_connect(request.app.state.database_path)) as connection:
            context = {
                "messages": connection.execute(
                    "SELECT * FROM contact_messages ORDER BY id DESC LIMIT 100"
                ).fetchall(),
                "registrations": connection.execute(
                    "SELECT * FROM webinar_registrations ORDER BY id DESC LIMIT 100"
                ).fetchall(),
                "bookings": connection.execute(
                    "SELECT * FROM consultation_bookings ORDER BY appointment_date, appointment_time LIMIT 100"
                ).fetchall(),
                "applications": connection.execute(
                    "SELECT * FROM job_applications ORDER BY id DESC LIMIT 100"
                ).fetchall(),
            }
        return _render(request, "admin.html", title="Admin dashboard", **context)

    @app.get("/admin/applications/{application_id}/{document}")
    def download_application_file(
        application_id: int,
        document: str,
        request: Request,
        _username: Annotated[str, Depends(_admin_credentials)],
    ) -> FileResponse:
        if document not in {"license", "resume"}:
            raise HTTPException(status_code=404, detail="File not found.")
        column = f"{document}_file"
        with closing(_connect(request.app.state.database_path)) as connection:
            row = connection.execute(
                f"SELECT {column} FROM job_applications WHERE id = ?",
                (application_id,),
            ).fetchone()
        if not row or not row[column]:
            raise HTTPException(status_code=404, detail="File not found.")
        path = (request.app.state.uploads_path / row[column]).resolve()
        if path.parent != request.app.state.uploads_path.resolve() or not path.is_file():
            raise HTTPException(status_code=404, detail="File not found.")
        return FileResponse(path, filename=f"application-{application_id}-{document}{path.suffix}")

    return app


app = create_app()
