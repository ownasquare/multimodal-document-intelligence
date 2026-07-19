"""Rendered, provider-free acceptance journey for the production-shaped stack."""

from __future__ import annotations

import os
import re
import time

import httpx
import pytest
from playwright.sync_api import Page, expect
from pytest_playwright import CreateContextCallback

SAMPLE_NAME = "Northstar Q2 Operations Review.pdf"
RECONCILIATION_QUESTION = "Do the chart months reconcile to the reported Q2 total?"
DEFAULT_API_URL = "http://127.0.0.1:18014"
DEFAULT_E2E_TOKEN = "e2e-local-only-token"
PREPARATION_TIMEOUT_SECONDS = 240

pytestmark = pytest.mark.e2e


def _browser_issues(page: Page) -> list[str]:
    issues: list[str] = []

    def capture_console(message: object) -> None:
        level = str(getattr(message, "type", ""))
        if level in {"warning", "error"}:
            issues.append(f"console {level}: {getattr(message, 'text', '')}")

    page.on("console", capture_console)
    page.on("pageerror", lambda error: issues.append(f"page error: {error}"))
    return issues


def _wait_for_workspace(api_url: str, token: str) -> None:
    deadline = time.monotonic() + PREPARATION_TIMEOUT_SECONDS
    last_status = "unavailable"
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(base_url=api_url, headers=headers, timeout=10.0) as client:
        duplicate = client.post("/api/v1/demo/sample")
        duplicate.raise_for_status()
        assert duplicate.json()["duplicate"] is True

        while time.monotonic() < deadline:
            response = client.get("/api/v1/status")
            response.raise_for_status()
            status = response.json()
            last_status = (
                f"{status['status']} with {status['ready_document_count']} ready document(s)"
            )
            if status["status"] == "ready" and status["ready_document_count"] == 1:
                return
            time.sleep(1)

    pytest.fail(
        f"The sample did not finish preparation within "
        f"{PREPARATION_TIMEOUT_SECONDS} seconds; last status: {last_status}."
    )


def _wait_for_product(page: Page, heading: str) -> None:
    expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible(timeout=30_000)
    expect(page).to_have_title("Document Intelligence")


def _assert_grounded_answer(page: Page) -> None:
    suggestion = page.get_by_role("button", name=RECONCILIATION_QUESTION, exact=True)
    expect(suggestion).to_be_visible()
    suggestion.click()

    question = page.get_by_label("Your question", exact=True)
    expect(question).to_have_value(RECONCILIATION_QUESTION)
    page.get_by_role("button", name="Ask documents", exact=True).click()

    expect(page.get_by_role("heading", name="Answer", exact=True)).to_be_visible(timeout=60_000)
    answer = page.locator(".docintel-answer")
    for expected in ("April $2.5M", "May $2.7M", "June $3.2M", "8.4M"):
        expect(answer).to_contain_text(expected)

    chart = page.locator("details").filter(has_text="page 3 · Chart")
    table_row = page.locator("details").filter(has_text="page 2 · Table row")
    expect(chart).to_have_count(1)
    expect(table_row).to_have_count(1)

    for expected in ("APR", "MAY", "JUN", "2.5M", "2.7M", "3.2M"):
        expect(chart).to_contain_text(expected)
    expect(chart.locator("img")).to_be_visible()
    expect(
        chart.get_by_text("The page preview is temporarily unavailable", exact=False)
    ).to_have_count(0)

    table_row.locator("summary").click()
    table_excerpt = table_row.locator("p").filter(has_text="Region: Total")
    expect(table_excerpt).to_contain_text("Net revenue ($M): 8.4")


def _assert_documents(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/?section=Documents", wait_until="domcontentloaded")
    _wait_for_product(page, "Documents")
    expect(page.get_by_role("heading", name=SAMPLE_NAME, exact=True)).to_be_visible()
    expect(page.get_by_text("Ready", exact=True)).to_be_visible()

    pages_metric = page.locator('[data-testid="stMetric"]').filter(has_text="Pages")
    evidence_metric = page.locator('[data-testid="stMetric"]').filter(has_text="Evidence items")
    expect(pages_metric).to_contain_text(re.compile(r"Pages\s*8"))
    expect(evidence_metric).to_contain_text(re.compile(r"Evidence items\s*[1-9]\d*"))


def _assert_privacy(page: Page) -> None:
    more = page.locator("details.docintel-more > summary")
    expect(more).to_be_visible()
    more.click()
    privacy = page.get_by_role("link", name="Privacy", exact=True)
    expect(privacy).to_be_visible()
    privacy.click()

    _wait_for_product(page, "Privacy and system")
    expect(page.get_by_text("Credential-free local sample", exact=True)).to_be_visible()
    expect(
        page.get_by_text(
            "Current mode does not send document content to an external model provider.",
            exact=True,
        )
    ).to_be_visible()


def test_sample_question_evidence_documents_and_privacy_on_desktop_and_phone(
    new_context: CreateContextCallback,
    base_url: str,
) -> None:
    api_url = os.getenv("DOCINTEL_E2E_API_URL", DEFAULT_API_URL)
    token = os.getenv("DOCINTEL_E2E_API_TOKEN", DEFAULT_E2E_TOKEN)
    desktop_context = new_context(viewport={"width": 1440, "height": 1000})
    phone_context = None

    try:
        desktop = desktop_context.new_page()
        desktop_issues = _browser_issues(desktop)
        desktop.goto(f"{base_url}/?section=Ask", wait_until="domcontentloaded")
        _wait_for_product(desktop, "Ask better questions of complex documents")

        desktop.get_by_role("button", name="Create sample workspace", exact=True).click()
        _wait_for_product(desktop, "Preparation")
        _wait_for_workspace(api_url, token)
        desktop.reload(wait_until="domcontentloaded")
        _wait_for_product(desktop, "Preparation")
        expect(desktop.get_by_text("Completed", exact=True)).to_be_visible()

        desktop.goto(f"{base_url}/?section=Ask", wait_until="domcontentloaded")
        _wait_for_product(desktop, "Ask across your documents")
        _assert_grounded_answer(desktop)
        _assert_documents(desktop, base_url)
        _assert_privacy(desktop)

        phone_context = new_context(
            viewport={"width": 390, "height": 844},
            screen={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
        )
        phone = phone_context.new_page()
        phone_issues = _browser_issues(phone)
        phone.goto(f"{base_url}/?section=Ask", wait_until="domcontentloaded")
        _wait_for_product(phone, "Ask across your documents")
        _assert_grounded_answer(phone)
        assert phone.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        _assert_documents(phone, base_url)
        assert phone.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        _assert_privacy(phone)
        assert phone.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )

        assert not desktop_issues, desktop_issues
        assert not phone_issues, phone_issues
    finally:
        if phone_context is not None:
            phone_context.close()
        desktop_context.close()
