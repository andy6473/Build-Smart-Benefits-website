import os
import re

import pytest
from playwright.sync_api import Page, expect


BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(
    ("path", "heading"),
    [
        ("/", re.compile(r"THE SMART\s*BENEFITS", re.IGNORECASE)),
        ("/about", re.compile(r"Your one-stop insurance brokerage partner", re.IGNORECASE)),
        ("/contact", re.compile(r"Contact us", re.IGNORECASE)),
        ("/rsvp", re.compile(r"Saturday in-person webinar", re.IGNORECASE)),
        ("/book", re.compile(r"Book a free consultation", re.IGNORECASE)),
        ("/careers", re.compile(r"Build a career around helping people", re.IGNORECASE)),
    ],
)
def test_public_pages_load(page: Page, path: str, heading: re.Pattern[str]) -> None:
    response = page.goto(f"{BASE_URL}{path}")

    assert response is not None and response.ok, f"{path} did not load successfully"
    expect(page.locator("h1")).to_contain_text(heading)


def test_home_page_links_to_booking_contact_and_careers(page: Page) -> None:
    page.goto(BASE_URL)

    expect(page.get_by_role("link", name=re.compile("Book a free consultation", re.I)).first).to_have_attribute(
        "href", "/book"
    )
    expect(page.get_by_role("link", name="Contact").first).to_have_attribute("href", "/contact")
    expect(page.get_by_role("link", name="Careers").first).to_have_attribute("href", "/careers")


def test_contact_form_exposes_required_fields(page: Page) -> None:
    page.goto(f"{BASE_URL}/contact")

    expect(page.locator('input[name="name"]')).to_have_attribute("required", "")
    expect(page.locator('input[name="email"]')).to_have_attribute("required", "")
    expect(page.locator('select[name="coverage_group"]')).to_have_attribute("required", "")
    expect(page.get_by_role("button", name=re.compile("Send message", re.I))).to_be_visible()


def test_webinar_registration_form_exposes_required_fields(page: Page) -> None:
    page.goto(f"{BASE_URL}/rsvp")

    expect(page.locator('input[name="name"]')).to_have_attribute("required", "")
    expect(page.locator('input[name="phone"]')).to_have_attribute("required", "")
    expect(page.locator('input[name="email"]')).to_have_attribute("required", "")
    expect(page.locator('select[name="agent_status"]')).to_have_attribute("required", "")


def test_booking_starts_with_a_calendar_and_requires_date_selection(page: Page) -> None:
    page.goto(f"{BASE_URL}/book")

    expect(page.get_by_role("heading", name=re.compile("Book a free consultation", re.I))).to_be_visible()
    expect(page.locator('[aria-label="Previous month"]')).to_be_visible()
    expect(page.locator('[aria-label="Next month"]')).to_be_visible()
    expect(page.get_by_text("Pick an available date to see appointment times.")).to_be_visible()
