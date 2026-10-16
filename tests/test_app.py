import re
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(
        database_path=tmp_path / "site.sqlite3",
        uploads_path=tmp_path / "uploads",
    )
    with TestClient(app) as test_client:
        yield test_client


def csrf_token(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, "Expected the page to include a CSRF token"
    return match.group(1)


def future_weekday() -> date:
    candidate = datetime.now(ZoneInfo("America/New_York")).date() + timedelta(days=1)
    while candidate.weekday() == 6:
        candidate += timedelta(days=1)
    return candidate


def test_all_public_pages_and_health_check_load(client: TestClient) -> None:
    for path in ("/", "/about", "/compare", "/contact", "/rsvp", "/book", "/careers"):
        response = client.get(path)
        assert response.status_code == 200
        assert "<h1" in response.text
    assert client.get("/health").json() == {"status": "ok"}


def test_comparison_page_explains_all_coverage_areas(client: TestClient) -> None:
    response = client.get("/compare")
    assert response.status_code == 200
    for heading in (
        "Business insurance",
        "Life &amp; health",
        "Personal insurance",
        "Retirement &amp; investment",
    ):
        assert heading in response.text
    assert "not equivalent policies" in response.text
    assert "not a quote, policy recommendation" in response.text


def test_contact_form_persists_valid_submission(client: TestClient) -> None:
    token = csrf_token(client.get("/contact"))
    response = client.post(
        "/contact",
        data={
            "csrf_token": token,
            "name": "Jordan Example",
            "email": "jordan@example.com",
            "phone": "7705550100",
            "coverage_group": "Investment Strategy",
            "message": "Please contact me about retirement planning.",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/contact?sent=1"

    with sqlite3.connect(client.app.state.database_path) as connection:
        row = connection.execute(
            "SELECT name, email, coverage_group FROM contact_messages"
        ).fetchone()
    assert row == ("Jordan Example", "jordan@example.com", "Investment Strategy")


def test_contact_form_rejects_invalid_email_and_invalid_csrf(client: TestClient) -> None:
    token = csrf_token(client.get("/contact"))
    response = client.post(
        "/contact",
        data={
            "csrf_token": token,
            "name": "Jordan Example",
            "email": "not-an-email",
            "coverage_group": "Investment Strategy",
        },
    )
    assert response.status_code == 422
    assert "valid email" in response.json()["detail"]

    response = client.post(
        "/contact",
        data={
            "csrf_token": "not-the-session-token",
            "name": "Jordan Example",
            "email": "jordan@example.com",
            "coverage_group": "Investment Strategy",
        },
    )
    assert response.status_code == 403


def test_rsvp_submission_is_stored(client: TestClient) -> None:
    token = csrf_token(client.get("/rsvp"))
    response = client.post(
        "/rsvp",
        data={
            "csrf_token": token,
            "name": "Jordan Example",
            "phone": "7705550100",
            "email": "jordan@example.com",
            "agent_status": "Not an Agent",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/rsvp?registered=1"


def test_booking_rejects_duplicate_slot(client: TestClient) -> None:
    appointment_day = future_weekday()
    calendar_month = appointment_day.strftime("%Y-%m")
    page = client.get(
        f"/book?month={calendar_month}&date={appointment_day.isoformat()}"
    )
    token = csrf_token(page)
    assert 'value="13:00">1:00 PM ET</option>' in page.text
    booking = {
        "csrf_token": token,
        "name": "Jordan Example",
        "phone": "7705550100",
        "email": "jordan@example.com",
        "appointment_date": appointment_day.isoformat(),
        "appointment_time": "09:00",
    }

    response = client.post("/book", data=booking, follow_redirects=False)
    assert response.status_code == 303
    duplicate = client.post("/book", data=booking)
    assert duplicate.status_code == 409


def test_booking_rejects_malformed_date_query(client: TestClient) -> None:
    response = client.get("/book?month=2026-10&date=not-a-date")
    assert response.status_code == 422


def test_career_application_stores_document_outside_public_static_path(
    client: TestClient,
) -> None:
    token = csrf_token(client.get("/careers"))
    response = client.post(
        "/careers",
        data={
            "csrf_token": token,
            "name": "Jordan Example",
            "phone": "7705550100",
            "email": "jordan@example.com",
            "address": "123 Main St, Atlanta, GA",
            "license_number": "GA-1234567",
        },
        files={"resume_file": ("resume.pdf", b"%PDF-test document", "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with sqlite3.connect(client.app.state.database_path) as connection:
        filename = connection.execute(
            "SELECT resume_file FROM job_applications"
        ).fetchone()[0]
    assert filename.endswith(".pdf")
    assert (client.app.state.uploads_path / filename).read_bytes() == b"%PDF-test document"
    assert not (client.app.state.uploads_path.parent / "static" / filename).exists()


def test_career_application_rejects_unsupported_document_type(client: TestClient) -> None:
    token = csrf_token(client.get("/careers"))
    response = client.post(
        "/careers",
        data={
            "csrf_token": token,
            "name": "Jordan Example",
            "phone": "7705550100",
            "email": "jordan@example.com",
            "address": "123 Main St, Atlanta, GA",
        },
        files={"resume_file": ("resume.exe", b"not a document", "application/octet-stream")},
    )
    assert response.status_code == 415
    with sqlite3.connect(client.app.state.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM job_applications").fetchone()[0] == 0


def test_admin_dashboard_requires_configured_credentials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    disabled = client.get("/admin", auth=("admin", "example"))
    assert disabled.status_code == 503

    monkeypatch.setenv("ADMIN_PASSWORD", "test-only-password")
    denied = client.get("/admin", auth=("admin", "incorrect"))
    assert denied.status_code == 401
    allowed = client.get("/admin", auth=("admin", "test-only-password"))
    assert allowed.status_code == 200
    assert "Contact messages" in allowed.text
