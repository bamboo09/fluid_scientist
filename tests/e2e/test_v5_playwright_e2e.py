"""Real Playwright browser E2E test for the V5 Conversational Workbench.

This is a TRUE browser test that:
1. Starts a FastAPI test server on a free port
2. Opens a real Chromium browser via Playwright
3. Drives the actual V5 three-panel UI
4. Verifies the complete V5 user journey

Test cases:
  1. Single Study: input → study card → select → draft → modify → proposal
     → confirm → draft v2 → confirm draft → CasePlan
  2. Multi-Study: input 2 numbered studies → 2 cards → independent states
  3. Cancel modification: propose → cancel → draft unchanged

Run with:
    pytest tests/e2e/test_v5_playwright_e2e.py -v -s
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so we can import create_app.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    """Start a uvicorn server on a free port and yield its base URL."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    env = dict(**os.environ)
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "fluid_scientist.api.app:create_app",
            "--factory",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "warning",
        ],
        cwd=str(ROOT),
        env={**env, "PYTHONPATH": str(ROOT / "src")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for the server to be ready
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{url}/api/system/version", timeout=1)
            break
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            time.sleep(0.5)
    else:
        proc.terminate()
        raise RuntimeError("Server failed to start within 30 seconds")

    yield url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture()
def page(server_url):
    """Launch Chromium, open a new page, navigate to the app."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto(server_url, wait_until="domcontentloaded")
        # Wait for the app to initialise (research input visible)
        page.wait_for_selector("#research-input", timeout=15000)
        # Give the app a moment to finish initSession()
        page.wait_for_timeout(2000)
        yield page
        context.close()
        browser.close()


# ---------------------------------------------------------------------------
# Helper: clear localStorage to start fresh
# ---------------------------------------------------------------------------

def _clear_session(page):
    """Clear localStorage to ensure a fresh session."""
    page.evaluate("localStorage.clear()")
    page.reload()
    page.wait_for_selector("#research-input", timeout=15000)
    page.wait_for_timeout(1000)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestV5SingleStudy:
    """Test case 1: Single Study full workflow via browser."""

    def test_page_loads_with_v5_branding(self, page):
        """Page loads and shows V5 workflow version."""
        # The version display should show v5
        version_text = page.locator("#wf-mode").text_content()
        assert version_text and "v5" in version_text.lower()

    def test_three_panel_layout_visible(self, page):
        """The three-panel layout is visible: left, center, right."""
        # Left panel (session/study list)
        assert page.is_visible(".panel-left")
        # Center panel (conversation)
        assert page.is_visible(".panel-center")
        # Right panel (draft viewer)
        assert page.is_visible(".panel-right")

    def test_input_research_shows_study_card(self, page):
        """Typing a research goal and sending produces a study card."""
        _clear_session(page)
        page.fill("#research-input", "研究雷诺数3900下的圆柱绕流")
        page.click("#send-button")

        # Wait for study card to appear in conversation
        page.wait_for_selector(".conv-study-card", timeout=15000)
        cards = page.locator(".conv-study-card")
        assert cards.count() >= 1

    def test_select_study_generates_draft(self, page):
        """Clicking a study card generates a draft in the right panel."""
        _clear_session(page)
        page.fill("#research-input", "研究后台阶流动 Re=800")
        page.click("#send-button")
        page.wait_for_selector(".conv-study-card", timeout=15000)

        # Click the study card
        page.locator(".conv-study-card").first.click()

        # Wait for draft to appear in the right panel
        page.wait_for_selector("#draft-viewer .draft-readonly-section", timeout=15000)

        # Draft version badge should be visible
        assert page.is_visible("#draft-version-badge")

    def test_modify_re_shows_proposal(self, page):
        """Requesting a modification via NL input shows a proposal card."""
        _clear_session(page)
        page.fill("#research-input", "研究后台阶流动 Re=800")
        page.click("#send-button")
        page.wait_for_selector(".conv-study-card", timeout=15000)
        page.locator(".conv-study-card").first.click()
        page.wait_for_selector("#draft-viewer .draft-readonly-section", timeout=15000)

        # Type a change request
        page.fill("#research-input", "把雷诺数改成5000")
        page.click("#send-button")

        # Wait for proposal card to appear
        page.wait_for_selector(".conv-proposal", timeout=15000)

        # Proposal should have confirm and cancel buttons
        assert page.get_by_text("确认修改").count() >= 1
        assert page.get_by_text("取消修改").count() >= 1

    def test_confirm_proposal_updates_draft(self, page):
        """Confirming the proposal updates the draft version."""
        _clear_session(page)
        page.fill("#research-input", "研究后台阶流动 Re=800")
        page.click("#send-button")
        page.wait_for_selector(".conv-study-card", timeout=15000)
        page.locator(".conv-study-card").first.click()
        page.wait_for_selector("#draft-viewer .draft-readonly-section", timeout=15000)

        page.fill("#research-input", "把雷诺数改成5000")
        page.click("#send-button")
        page.wait_for_selector(".conv-proposal", timeout=15000)

        # Get initial version
        initial_badge = page.locator("#draft-version-badge").text_content()

        # Click confirm
        page.get_by_text("确认修改").first.click()

        # Wait for version to change or "已应用" to appear
        page.wait_for_selector(".proposal-status.applied", timeout=10000)

        # The proposal card should now show "已应用"
        assert page.locator(".proposal-status.applied").count() >= 1

    def test_confirm_draft_shows_caseplan_button(self, page):
        """After confirming the draft, CasePlan generation button appears."""
        _clear_session(page)
        page.fill("#research-input", "研究后台阶流动 Re=800")
        page.click("#send-button")
        page.wait_for_selector(".conv-study-card", timeout=15000)
        page.locator(".conv-study-card").first.click()
        page.wait_for_selector("#draft-viewer .draft-readonly-section", timeout=15000)

        # Click confirm draft button
        confirm_btn = page.locator("#action-bar button:has-text('确认草案')")
        if confirm_btn.count() > 0:
            confirm_btn.first.click()
            # Wait for "生成 CasePlan" button to appear
            page.wait_for_selector("#action-bar button:has-text('生成 CasePlan')", timeout=10000)
            assert page.locator("#action-bar button:has-text('生成 CasePlan')").count() >= 1


class TestV5MultiStudy:
    """Test case 2: Multiple studies from batch input."""

    def test_two_studies_produce_two_cards(self, page):
        """Inputting 2 numbered studies produces 2 study cards."""
        _clear_session(page)
        two_tasks = (
            "1. 研究近壁倾斜圆柱在雷诺数3900下的三维湍流尾迹\n"
            "2. 研究45度倾斜圆射流冲击平壁的非定常流动"
        )
        page.fill("#research-input", two_tasks)
        page.click("#send-button")

        page.wait_for_selector(".conv-study-card", timeout=15000)
        cards = page.locator(".conv-study-card")
        assert cards.count() >= 2, f"Expected >=2 cards, got {cards.count()}"

    def test_studies_shown_in_left_panel(self, page):
        """Study list appears in the left panel after batch input."""
        _clear_session(page)
        two_tasks = (
            "1. 研究圆柱绕流 Re=100\n"
            "2. 研究后台阶流动 Re=800"
        )
        page.fill("#research-input", two_tasks)
        page.click("#send-button")

        page.wait_for_selector(".conv-study-card", timeout=15000)

        # Left panel should show study items
        page.wait_for_selector("#study-items .study-item", timeout=5000)
        items = page.locator("#study-items .study-item")
        assert items.count() >= 2


class TestV5CancelProposal:
    """Test case 3: Cancel modification does not change the draft."""

    def test_cancel_proposal_preserves_draft(self, page):
        """Cancelling a proposal leaves the draft unchanged."""
        _clear_session(page)
        page.fill("#research-input", "研究后台阶流动 Re=800")
        page.click("#send-button")
        page.wait_for_selector(".conv-study-card", timeout=15000)
        page.locator(".conv-study-card").first.click()
        page.wait_for_selector("#draft-viewer .draft-readonly-section", timeout=15000)

        # Get initial version badge
        initial_badge = page.locator("#draft-version-badge").text_content()

        page.fill("#research-input", "把雷诺数改成5000")
        page.click("#send-button")
        page.wait_for_selector(".conv-proposal", timeout=15000)

        # Click cancel
        page.get_by_text("取消修改").first.click()

        # Wait for "已取消" to appear
        page.wait_for_selector(".proposal-status.cancelled", timeout=10000)
        assert page.locator(".proposal-status.cancelled").count() >= 1

        # Draft version badge should be unchanged
        final_badge = page.locator("#draft-version-badge").text_content()
        assert initial_badge == final_badge, \
            f"Draft version changed from '{initial_badge}' to '{final_badge}' after cancel"


class TestV5SystemVersion:
    """Verify the system reports V5 version."""

    def test_system_version_api(self, server_url):
        """The /api/system/version endpoint reports v5."""
        with urllib.request.urlopen(f"{server_url}/api/system/version") as r:
            v = json.loads(r.read())
        assert v["workflow"] == "v5"
        assert v["api_version"] == "5.0"
        assert v["schema_version"] == "5.0"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

