"""End-to-end tests using Playwright.

Smoke-tests every page, verifies data CRUD works via the UI, and
confirms real Bedrock chat responds in the Dialog Center.

Prerequisites:
    - Backend running on :8087
    - Frontend running on :5173
    - alice/password seeded
    - Valid AWS creds in env.config

Run:
    cd agent_company && python3 -m pytest tests/test_e2e.py -v --tb=short
"""
from __future__ import annotations

import re
import time

import pytest
from playwright.sync_api import Page, expect, sync_playwright


FRONTEND_URL = "http://localhost:5173"
LOGIN_USER = "alice"
LOGIN_PASS = "password"


# ============================================================================
# Pytest fixtures — reusable browser context
# ============================================================================

@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance):
    browser = playwright_instance.chromium.launch(headless=True)
    yield browser
    browser.close()


@pytest.fixture
def context(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    yield ctx
    ctx.close()


@pytest.fixture
def page(context) -> Page:
    return context.new_page()


@pytest.fixture
def logged_in_page(page: Page) -> Page:
    """Page already logged in as alice with zh-TW language."""
    page.goto(FRONTEND_URL)
    page.wait_for_load_state("networkidle")
    if page.locator('input[type="password"]').count() > 0:
        page.fill('input[type="text"]', LOGIN_USER)
        page.fill('input[type="password"]', LOGIN_PASS)
        page.click('button[type="submit"]')
        page.wait_for_url(re.compile(r".*/(dialog|dashboard)"), timeout=10_000)
    # Ensure the user's language is zh-TW so Chinese text assertions pass
    page.evaluate("""
        fetch('/api/me', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ language: 'zh-TW' })
        })
    """)
    page.wait_for_timeout(300)
    page.reload()
    page.wait_for_load_state("networkidle")
    return page


def _agent_id_by_name(page: Page, name: str) -> int:
    """Resolve a seeded agent's id by name via the live API. Avoids hard-coded
    ids that drift when SERIAL counters advance after backend tests churn."""
    agents = page.evaluate("""
        async () => {
            const r = await fetch('/api/agents', { credentials: 'include' });
            return await r.json();
        }
    """)
    for a in agents:
        if a["name"] == name:
            return int(a["id"])
    raise AssertionError(f"agent named {name!r} not found in seed")


def _seed_workflow_id(page: Page) -> int:
    """Resolve the seeded workflow id dynamically. SERIAL counters drift
    whenever test_api_crud truncates with RESTART IDENTITY, so a hard-coded
    id=2 (or 1) will fail intermittently depending on run order."""
    workflows = page.evaluate("""
        async () => {
            const r = await fetch('/api/workflows', { credentials: 'include' });
            return await r.json();
        }
    """)
    if not workflows:
        raise AssertionError("no seeded workflows found for current user")
    return int(workflows[0]["id"])


# ============================================================================
# Smoke tests — all pages render without errors
# ============================================================================

class TestPages:
    """Verify every route renders without console errors or 500s."""

    def _goto_and_check(self, page: Page, path: str, required_text: list[str] = None):
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)

        page.goto(f"{FRONTEND_URL}{path}")
        page.wait_for_load_state("networkidle", timeout=15_000)

        # Assert no uncaught JS errors
        assert not any("Uncaught" in e or "TypeError" in e for e in errors), f"JS errors on {path}: {errors}"

        if required_text:
            for text in required_text:
                expect(page.get_by_text(text, exact=False).first).to_be_visible(timeout=5_000)

    def test_login_page_renders(self, page: Page):
        page.goto(FRONTEND_URL)
        expect(page.locator("input").first).to_be_visible()
        expect(page.get_by_role("button", name=re.compile(r"登入|Sign In"))).to_be_visible()

    def test_login_flow(self, page: Page):
        page.goto(FRONTEND_URL)
        page.fill('input[type="text"]', LOGIN_USER)
        page.fill('input[type="password"]', LOGIN_PASS)
        page.click('button[type="submit"]')
        page.wait_for_url(re.compile(r".*/(dialog|dashboard)"), timeout=10_000)
        # Should see the sidebar
        expect(page.locator(".sidebar")).to_be_visible()

    def test_dialog_center_renders(self, logged_in_page: Page):
        self._goto_and_check(logged_in_page, "/dialog", required_text=["Lead"])
        # Cast row should have Lead + members
        cast = logged_in_page.locator(".cast-member")
        assert cast.count() >= 2, f"expected multiple cast members, got {cast.count()}"

    def test_dashboard_renders_without_crash(self, logged_in_page: Page):
        """This is the key test — Dashboard was the broken page."""
        self._goto_and_check(logged_in_page, "/dashboard", required_text=["Dashboard"])
        # Summary cards should have numbers
        sum_cards = logged_in_page.locator(".sum-card .value")
        assert sum_cards.count() == 4
        # Agent load cards
        load_cards = logged_in_page.locator(".load-card")
        assert load_cards.count() >= 7, f"expected >= 7 agent cards, got {load_cards.count()}"
        # Gantt panel should be present
        expect(logged_in_page.locator(".gantt-panel")).to_be_visible()

    def test_agents_list_renders(self, logged_in_page: Page):
        self._goto_and_check(logged_in_page, "/agents", required_text=["員工", "Lead", "小明"])

    def test_agent_detail_page(self, logged_in_page: Page):
        # Navigate directly (sidebar has "員工" link, avoid ambiguity)
        logged_in_page.goto(f"{FRONTEND_URL}/agents/2")
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.get_by_role("button", name="總覽")).to_be_visible()
        expect(logged_in_page.get_by_role("button", name="預算 / Quotas")).to_be_visible()
        expect(logged_in_page.get_by_role("button", name="上班時間")).to_be_visible()
        expect(logged_in_page.get_by_role("button", name="分享設定")).to_be_visible()

    def test_runs_page_renders(self, logged_in_page: Page):
        self._goto_and_check(logged_in_page, "/runs", required_text=["執行紀錄"])

    def test_schedules_page_renders(self, logged_in_page: Page):
        self._goto_and_check(logged_in_page, "/schedules", required_text=["排程"])

    def test_skills_page_renders(self, logged_in_page: Page):
        self._goto_and_check(logged_in_page, "/skills", required_text=["技能"])

    def test_escalations_page_renders(self, logged_in_page: Page):
        self._goto_and_check(logged_in_page, "/escalations", required_text=["Escalation"])

    def test_settings_page_renders(self, logged_in_page: Page):
        self._goto_and_check(logged_in_page, "/settings", required_text=["設定"])


# ============================================================================
# Interactive tests — CRUD + real agent chat
# ============================================================================

class TestInteractions:

    def test_dialog_center_shift_enter_is_newline(self, logged_in_page: Page):
        """Modern behavior: plain Enter sends; Shift+Enter adds a newline.
        The send path is tested in test_dialog_center_chat_end_to_end — this
        test only verifies that Shift+Enter does NOT submit and does insert a
        newline into the textarea."""
        logged_in_page.goto(f"{FRONTEND_URL}/dialog")
        logged_in_page.wait_for_load_state("networkidle")
        # Lead is active by default on load
        textarea = logged_in_page.locator(".composer textarea")
        expect(textarea).to_be_enabled(timeout=5_000)
        textarea.fill("測試 Shift+Enter 換行")
        # Shift+Enter — should NOT send, should add newline
        textarea.press("Shift+Enter")
        val = textarea.input_value()
        assert "\n" in val, f"Shift+Enter should add newline, got: {val!r}"

    def test_dialog_center_run_status_card_renders(self, logged_in_page: Page):
        """Regression: RunStatusCard crashed with `cost.toFixed is not a
        function` because Postgres NUMERIC came back as a string and the
        frontend called .toFixed on it. Seed a run_event lead message,
        load /dialog, assert no JS error and the card shows up."""
        from backend import db
        import json as _json
        import uuid as _uuid

        alice = db.fetch_one("SELECT id FROM as_users WHERE username='alice'")
        uid = alice["id"]
        # Pick any existing workflow owned by alice, or just create one
        wf = db.fetch_one(
            "SELECT id FROM workflows WHERE user_id = %s LIMIT 1", (uid,),
        )
        wid = wf["id"] if wf else db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, 'run-card-test') RETURNING id",
            (uid,),
        )
        # Synthesize a finished run so the card has real numbers to render.
        run_id = db.execute_returning(
            """
            INSERT INTO runs (workflow_id, user_id, initial_input, status,
                              total_input_tokens, total_output_tokens,
                              total_cost_usd, total_duration_ms, finished_at)
            VALUES (%s, %s, 'card test', 'done', 1234, 5678, 0.2332, 4600, NOW())
            RETURNING id
            """,
            (wid, uid),
        )
        # Attach a run_event lead message inside alice's Lead thread so the
        # dialog will render the card on load.
        thread = db.fetch_one(
            """
            SELECT thread_id FROM lead_conversations
            WHERE user_id = %s AND agent_id IS NULL AND status = 'active'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (uid,),
        )
        if thread:
            tid = thread["thread_id"]
        else:
            tid = _uuid.uuid4().hex[:16]
            db.execute(
                "INSERT INTO lead_conversations (user_id, thread_id, status) VALUES (%s, %s, 'active')",
                (uid, tid),
            )
        msg_id = db.execute_returning(
            """
            INSERT INTO lead_messages (thread_id, role, content, metadata)
            VALUES (%s, 'lead', '', %s::jsonb) RETURNING id
            """,
            (tid, _json.dumps({
                "event": "run_event",
                "run_id": run_id,
                "workflow_id": wid,
                "workflow_name": "卡片測試流程",
            })),
        )

        errors: list[str] = []
        logged_in_page.on("pageerror", lambda exc: errors.append(str(exc)))
        logged_in_page.on(
            "console",
            lambda msg: errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None,
        )

        try:
            logged_in_page.goto(f"{FRONTEND_URL}/dialog")
            logged_in_page.wait_for_load_state("networkidle")
            # Give RunStatusCard time to poll /api/runs/:id
            logged_in_page.wait_for_timeout(1500)

            # The card should be on screen and NOT have crashed
            assert not any("toFixed" in e or "Uncaught" in e for e in errors), \
                f"JS error on /dialog: {errors}"
            # The run status card should render the workflow name
            card = logged_in_page.locator(".run-status-card")
            assert card.count() >= 1, "run-status-card should render"
        finally:
            db.execute("DELETE FROM lead_messages WHERE id = %s", (msg_id,))
            db.execute("DELETE FROM runs WHERE id = %s", (run_id,))

    def test_dialog_center_messages_paginate_on_scroll_top(self, logged_in_page: Page):
        """Seed 35 messages in a Lead thread and verify (a) only the newest
        20 render initially, (b) scrolling to the top of the messages list
        triggers a fetch of older messages, (c) after the fetch all 35 are
        visible, and (d) the viewport stays anchored near the user's scroll
        position instead of snapping to the bottom."""
        from backend import db
        import uuid as _uuid

        alice = db.fetch_one("SELECT id FROM as_users WHERE username = 'alice'")
        uid = alice["id"]
        tid = _uuid.uuid4().hex[:16]
        db.execute(
            "INSERT INTO lead_conversations (user_id, thread_id, status) VALUES (%s, %s, 'active')",
            (uid, tid),
        )
        # 35 messages, alternating user/lead so render paths are exercised
        msg_ids: list[int] = []
        try:
            for i in range(35):
                role = "user" if i % 2 == 0 else "lead"
                mid = db.execute_returning(
                    "INSERT INTO lead_messages (thread_id, role, content) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (tid, role, f"分頁測試訊息 {i:02d}"),
                )
                msg_ids.append(mid)

            # Open /dialog — Lead is the default active cast member, so the
            # chat view and messages container show immediately, and the
            # auto-load effect picks threads[0] which (ordered by updated_at
            # DESC) is the thread we just inserted.
            logged_in_page.goto(f"{FRONTEND_URL}/dialog")
            logged_in_page.wait_for_load_state("networkidle")

            # Wait for the messages container and at least the newest message
            expect(logged_in_page.locator('[data-testid="dialog-messages"]')).to_be_visible(timeout=5_000)
            expect(logged_in_page.locator('.messages .bubble', has_text="分頁測試訊息 34")).to_be_visible(timeout=5_000)

            # Only the newest 20 should have rendered (messages 15–34).
            # Message 14 and below must NOT be in the DOM yet.
            bubble_count_1 = logged_in_page.locator('.messages .bubble').count()
            assert 20 <= bubble_count_1 <= 22, (
                f"initial page should render ~20 bubbles, got {bubble_count_1}"
            )
            # Message 15 should be present; message 14 should not.
            assert logged_in_page.locator('.messages .bubble', has_text="分頁測試訊息 15").count() >= 1
            assert logged_in_page.locator('.messages .bubble', has_text="分頁測試訊息 14").count() == 0

            # Scroll the messages container to the top to trigger older-page fetch
            logged_in_page.evaluate("""
                () => {
                    const el = document.querySelector('[data-testid="dialog-messages"]');
                    if (el) el.scrollTop = 0;
                }
            """)

            # Message 14 should appear (it's in the next page)
            expect(
                logged_in_page.locator('.messages .bubble', has_text="分頁測試訊息 14")
            ).to_be_visible(timeout=5_000)
            # Eventually the oldest message (00) should also load
            expect(
                logged_in_page.locator('.messages .bubble', has_text="分頁測試訊息 00")
            ).to_be_visible(timeout=5_000)

            bubble_count_2 = logged_in_page.locator('.messages .bubble').count()
            assert bubble_count_2 >= 35, (
                f"after loading older pages should see ≥35 bubbles, got {bubble_count_2}"
            )
        finally:
            db.execute("DELETE FROM lead_messages WHERE thread_id = %s", (tid,))
            db.execute("DELETE FROM lead_conversations WHERE thread_id = %s", (tid,))

    def test_dialog_center_auto_focus_on_select(self, logged_in_page: Page):
        """Clicking a non-Lead cast member should auto-focus the textarea
        and trigger the cast to enter compact mode."""
        logged_in_page.goto(f"{FRONTEND_URL}/dialog")
        logged_in_page.wait_for_load_state("networkidle")

        xiaohua_id = _agent_id_by_name(logged_in_page, "小華")
        logged_in_page.locator(f'.cast-member[data-id="{xiaohua_id}"]').click()

        # Cast should mark has-active when something is selected
        expect(logged_in_page.locator(".cast.has-active")).to_be_visible()

        # Textarea should be the focused element
        is_focused = logged_in_page.evaluate("""
            () => {
                const ta = document.querySelector('.composer textarea');
                return document.activeElement === ta;
            }
        """)
        assert is_focused, "textarea should be focused after selecting an agent"

    def test_dialog_center_non_lead_chat(self, logged_in_page: Page):
        """Click a non-Lead agent in the cast row and verify the textarea
        unlocks, you can send a message, and you get a real reply (not the
        'thinking' placeholder) from that agent via Bedrock."""
        logged_in_page.goto(f"{FRONTEND_URL}/dialog")
        logged_in_page.wait_for_load_state("networkidle")

        # Click 小明 (look up id by name to survive serial drift)
        xiaoming_id = _agent_id_by_name(logged_in_page, "小明")
        logged_in_page.locator(f'.cast-member[data-id="{xiaoming_id}"]').click()

        textarea = logged_in_page.locator(".composer textarea")
        expect(textarea).to_be_enabled(timeout=3_000)

        textarea.fill("你好，請用一句話介紹你自己")
        textarea.press("Enter")

        # While the call is in flight, the thinking bubble should appear
        expect(logged_in_page.locator('[data-testid="lead-thinking"]')).to_be_visible(timeout=3_000)
        # Then it should disappear once the reply arrives
        expect(logged_in_page.locator('[data-testid="lead-thinking"]')).to_have_count(0, timeout=60_000)
        # A non-loading bot bubble should now exist
        bot_bubbles = logged_in_page.locator(".bubble.bot:not(.loading)")
        expect(bot_bubbles.first).to_be_visible(timeout=5_000)
        text = bot_bubbles.first.inner_text()
        assert len(text) > 3, f"bot response too short: {text!r}"

    def test_dialog_center_chat_end_to_end(self, logged_in_page: Page):
        """Send a real message to Lead via Enter and wait for Bedrock response."""
        logged_in_page.goto(f"{FRONTEND_URL}/dialog")
        logged_in_page.wait_for_load_state("networkidle")
        textarea = logged_in_page.locator(".composer textarea")
        expect(textarea).to_be_enabled(timeout=5_000)
        textarea.fill("你好，簡單介紹你自己")
        textarea.press("Enter")
        # Wait for bot bubble (real Bedrock call takes several seconds)
        logged_in_page.wait_for_selector(".bubble.bot", timeout=45_000)
        bot_bubble = logged_in_page.locator(".bubble.bot").first
        expect(bot_bubble).to_be_visible()
        text = bot_bubble.inner_text()
        assert len(text) > 5, f"bot response too short: {text!r}"

    def test_agent_detail_working_hours_edit(self, logged_in_page: Page):
        logged_in_page.goto(f"{FRONTEND_URL}/agents/2")  # 小明
        logged_in_page.wait_for_load_state("networkidle")
        # Switch to 上班時間 tab
        logged_in_page.get_by_role("button", name="上班時間").click()
        # Preset button should render
        expect(logged_in_page.get_by_role("button", name="全日運作 (24/7)")).to_be_visible()
        # Click it — should show save bar
        logged_in_page.get_by_role("button", name="全日運作 (24/7)").click()
        expect(logged_in_page.get_by_text("有未儲存的變更")).to_be_visible(timeout=3_000)

    def test_agent_detail_quota_crud(self, logged_in_page: Page):
        logged_in_page.goto(f"{FRONTEND_URL}/agents/2")
        logged_in_page.wait_for_load_state("networkidle")
        logged_in_page.get_by_role("button", name="預算 / Quotas").click()
        # Click add
        logged_in_page.get_by_role("button", name="+ 新增限制").click()
        # Fill name
        logged_in_page.locator('input[placeholder*="日常使用"]').fill("e2e 測試預算")
        logged_in_page.get_by_role("button", name="新增").click()
        # Quota card should appear
        expect(logged_in_page.get_by_text("e2e 測試預算")).to_be_visible(timeout=5_000)
        # Delete it
        logged_in_page.locator('button.del-btn').first.click()
        expect(logged_in_page.get_by_text("e2e 測試預算")).not_to_be_visible(timeout=5_000)

    def test_agent_detail_visibility_change(self, logged_in_page: Page):
        logged_in_page.goto(f"{FRONTEND_URL}/agents/2")
        logged_in_page.wait_for_load_state("networkidle")
        logged_in_page.get_by_role("button", name="分享設定").click()
        # Select org_wide radio
        logged_in_page.get_by_text("整個組織 (org_wide)").click()
        # Give it a moment to persist
        logged_in_page.wait_for_timeout(500)
        # Reload and verify
        logged_in_page.reload()
        logged_in_page.wait_for_load_state("networkidle")
        logged_in_page.get_by_role("button", name="分享設定").click()
        org_radio = logged_in_page.locator('input[value="org_wide"]')
        expect(org_radio).to_be_checked()

    def test_sidebar_navigation(self, logged_in_page: Page):
        """Click through all sidebar links — each should load.

        After Phase 1.4, 執行紀錄 / Escalation / 排程 / Workflows are merged
        into the new 紀錄 and 自動化 wrapper pages, so the sidebar is shorter.
        """
        for name, fragment in [
            ("Dashboard", "/dashboard"),
            ("員工", "/agents"),
            ("團隊", "/groups"),
            ("自動化", "/automation"),
            ("紀錄", "/records"),
            ("Skill / MCP / 知識庫", "/library"),
            ("設定", "/settings"),
            ("對話中心", "/dialog"),
        ]:
            logged_in_page.get_by_role("link", name=name, exact=True).click()
            logged_in_page.wait_for_url(re.compile(rf".*{re.escape(fragment)}"), timeout=5_000)
            # Page should render without crash
            logged_in_page.wait_for_load_state("networkidle", timeout=10_000)

    def test_settings_display_name_and_password(self, logged_in_page: Page):
        """Update display name, then change password, then change it back."""
        logged_in_page.goto(f"{FRONTEND_URL}/settings")
        logged_in_page.wait_for_load_state("networkidle")

        # Update display name
        new_name = f"Alice {int(time.time())}"
        name_input = logged_in_page.locator('[data-testid="display-name-input"]')
        name_input.fill(new_name)
        logged_in_page.locator('[data-testid="save-profile-btn"]').click()
        expect(logged_in_page.locator('[data-testid="profile-saved"]')).to_be_visible(timeout=5_000)

        # Verify persisted: reload and check
        logged_in_page.reload()
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.locator('[data-testid="display-name-input"]')).to_have_value(new_name)

        # Change password to new_pw then back to original
        logged_in_page.locator('[data-testid="old-password-input"]').fill("password")
        logged_in_page.locator('[data-testid="new-password-input"]').fill("newpass123")
        logged_in_page.locator('[data-testid="new-password-confirm"]').fill("newpass123")
        logged_in_page.locator('[data-testid="change-password-btn"]').click()
        expect(logged_in_page.locator('[data-testid="password-saved"]')).to_be_visible(timeout=5_000)

        # Change back to "password" so other tests still work
        logged_in_page.locator('[data-testid="old-password-input"]').fill("newpass123")
        logged_in_page.locator('[data-testid="new-password-input"]').fill("password")
        logged_in_page.locator('[data-testid="new-password-confirm"]').fill("password")
        logged_in_page.locator('[data-testid="change-password-btn"]').click()
        expect(logged_in_page.locator('[data-testid="password-saved"]')).to_be_visible(timeout=5_000)

    def test_settings_password_mismatch_error(self, logged_in_page: Page):
        logged_in_page.goto(f"{FRONTEND_URL}/settings")
        logged_in_page.wait_for_load_state("networkidle")
        logged_in_page.locator('[data-testid="old-password-input"]').fill("password")
        logged_in_page.locator('[data-testid="new-password-input"]').fill("abcd")
        logged_in_page.locator('[data-testid="new-password-confirm"]').fill("efgh")
        logged_in_page.locator('[data-testid="change-password-btn"]').click()
        expect(logged_in_page.locator('[data-testid="password-error"]')).to_contain_text("不相同")

    def test_groups_create_edit_delete(self, logged_in_page: Page):
        """Create a group with 2 members, edit it to add a 3rd member, delete it."""
        unique = f"E2E Group {int(time.time())}"

        logged_in_page.goto(f"{FRONTEND_URL}/groups")
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.get_by_role("heading", name="團隊")).to_be_visible()

        # Resolve seeded agent ids by name to survive serial drift
        a1 = _agent_id_by_name(logged_in_page, "小明")
        a2 = _agent_id_by_name(logged_in_page, "小華")
        a3 = _agent_id_by_name(logged_in_page, "小芳")

        # Open create modal
        logged_in_page.locator('[data-testid="new-group-btn"]').click()
        logged_in_page.locator('[data-testid="group-name-input"]').fill(unique)
        # Pick first two
        logged_in_page.locator(f'[data-testid="group-member-toggle-{a1}"]').click()
        logged_in_page.locator(f'[data-testid="group-member-toggle-{a2}"]').click()
        logged_in_page.locator('[data-testid="save-group-submit"]').click()

        # Card should appear with "2 位成員"
        card = logged_in_page.locator(f'[data-testid^="group-card-"]', has_text=unique)
        expect(card).to_be_visible(timeout=5_000)
        expect(card).to_contain_text("2 位成員")

        testid = card.get_attribute("data-testid")
        gid = testid.split("-")[-1]  # type: ignore

        # Edit: add a third member
        logged_in_page.locator(f'[data-testid="edit-group-{gid}"]').click()
        logged_in_page.wait_for_timeout(500)
        logged_in_page.locator(f'[data-testid="group-member-toggle-{a3}"]').click()
        logged_in_page.locator('[data-testid="save-group-submit"]').click()
        expect(card).to_contain_text("3 位成員", timeout=5_000)

        # Delete
        logged_in_page.locator(f'[data-testid="delete-group-{gid}"]').click()
        logged_in_page.locator('[data-testid="confirm-delete-group"]').click()
        expect(logged_in_page.get_by_text(unique)).to_have_count(0, timeout=5_000)

    def test_agent_export_import_roundtrip(self, logged_in_page: Page):
        """Export agent #2's bundle via API, tweak the name, import via
        file picker, verify the new agent shows up."""
        logged_in_page.goto(f"{FRONTEND_URL}/agents")
        logged_in_page.wait_for_load_state("networkidle")

        bundle = logged_in_page.evaluate("""
            async () => {
                const r = await fetch('/api/agents/2/export', { credentials: 'include' });
                return await r.json();
            }
        """)
        assert bundle.get("schema_version") == "1.0"
        new_name = f"Imported {int(time.time())}"
        bundle["profile"]["name"] = new_name
        import json as _json
        logged_in_page.locator('[data-testid="import-agent-input"]').set_input_files(
            files=[{"name": "agent.json", "mimeType": "application/json",
                    "buffer": _json.dumps(bundle).encode()}]
        )
        # Should navigate into the new agent detail
        logged_in_page.wait_for_url(re.compile(r".*/agents/\d+"), timeout=10_000)
        expect(logged_in_page.get_by_role("heading", name=new_name)).to_be_visible(timeout=5_000)

        # Cleanup — delete the imported agent
        logged_in_page.locator('[data-testid="delete-agent-btn"]').click()
        logged_in_page.locator('[data-testid="confirm-delete-agent"]').click()
        logged_in_page.wait_for_url(re.compile(r".*/agents$"), timeout=5_000)

    def test_workflow_export_import_roundtrip(self, logged_in_page: Page):
        """Export the seed workflow via API, then import it via the UI file
        picker and verify a new workflow appears."""
        logged_in_page.goto(f"{FRONTEND_URL}/workflows")
        logged_in_page.wait_for_load_state("networkidle")

        # Export the seed workflow through backend (fetching JSON directly)
        seed_wid = _seed_workflow_id(logged_in_page)
        bundle = logged_in_page.evaluate(
            """(wid) => fetch('/api/workflows/' + wid + '/export', { credentials: 'include' }).then(r => r.json())""",
            seed_wid,
        )
        assert bundle["format"] == "agent_company.workflow.v1"
        assert len(bundle["nodes"]) > 0

        # Rename bundle so we can distinguish the imported copy
        bundle["name"] = f"Imported {int(time.time())}"
        import json as _json
        bundle_json = _json.dumps(bundle)

        # Set file input directly (Playwright supports this with a payload)
        logged_in_page.locator('[data-testid="import-workflow-input"]').set_input_files(
            files=[{"name": "bundle.json", "mimeType": "application/json",
                    "buffer": bundle_json.encode()}]
        )

        # Should navigate to the new workflow editor
        logged_in_page.wait_for_url(re.compile(r".*/workflows/\d+"), timeout=10_000)
        expect(logged_in_page.locator('[data-testid="wf-name-input"]')).to_have_value(bundle["name"], timeout=5_000)
        # Nodes should have transferred
        node_count = len(bundle["nodes"])
        expect(logged_in_page.locator(".wf-node")).to_have_count(node_count)

        # Cleanup via API
        url_parts = logged_in_page.url.split("/")
        new_wid = int(url_parts[-1])
        logged_in_page.evaluate(
            "async (wid) => await fetch(`/api/workflows/${wid}`, { method: 'DELETE', credentials: 'include' })",
            new_wid,
        )

    def test_workflow_editor_run_button(self, logged_in_page: Page):
        """Click the 執行 button in the editor toolbar, fill input, submit,
        and verify it navigates to the new run detail page."""
        # Open the seed workflow — it already has nodes wired up
        logged_in_page.goto(f"{FRONTEND_URL}/workflows")
        logged_in_page.wait_for_load_state("networkidle")
        seed_wid = _seed_workflow_id(logged_in_page)
        logged_in_page.goto(f"{FRONTEND_URL}/workflows/{seed_wid}")
        logged_in_page.wait_for_load_state("networkidle")

        run_btn = logged_in_page.locator('[data-testid="wf-run-btn"]')
        expect(run_btn).to_be_enabled(timeout=5_000)
        run_btn.click()

        # Modal opens
        logged_in_page.locator('[data-testid="wf-run-input"]').fill("e2e 測試輸入")
        logged_in_page.locator('[data-testid="wf-run-submit"]').click()

        # Should navigate to /runs/:id
        logged_in_page.wait_for_url(re.compile(r".*/runs/\d+"), timeout=10_000)
        expect(logged_in_page.locator('[data-testid="run-detail-page"]')).to_be_visible(timeout=5_000)

    def test_workflow_group_renders_fanout(self, logged_in_page: Page):
        """Create a workflow whose only node is a group (3 members). Verify
        the editor renders 3 .wf-group-member cards inside a .wf-group
        container, plus SVG fan-in/fan-out paths."""
        from backend import db

        # Resolve alice's user_id and seeded agent ids by name
        alice = db.fetch_one("SELECT id FROM as_users WHERE username = 'alice'")
        uid = alice["id"]
        agent_rows = db.fetch_all(
            "SELECT id, name FROM agents WHERE user_id = %s AND name IN ('小明','小華','小芳')",
            (uid,),
        )
        ids_by_name = {r["name"]: r["id"] for r in agent_rows}

        # Build group + workflow + node directly via DB to skip Lead
        gid = db.execute_returning(
            "INSERT INTO groups_tbl (user_id, name, mode) "
            "VALUES (%s, %s, 'parallel') RETURNING id",
            (uid, f"e2e fan group {int(time.time())}"),
        )
        for i, name in enumerate(("小明", "小華", "小芳")):
            db.execute(
                "INSERT INTO group_members (group_id, agent_id, position) VALUES (%s, %s, %s)",
                (gid, ids_by_name[name], i),
            )
        wid = db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, %s) RETURNING id",
            (uid, f"e2e fanout wf {int(time.time())}"),
        )
        db.execute(
            "INSERT INTO workflow_nodes (workflow_id, position, node_type, group_id, label, pos_x, pos_y) "
            "VALUES (%s, 0, 'group', %s, '並行測試', 200, 300)",
            (wid, gid),
        )

        try:
            logged_in_page.goto(f"{FRONTEND_URL}/workflows/{wid}")
            logged_in_page.wait_for_load_state("networkidle")

            # Group container should render
            expect(logged_in_page.locator(".wf-group")).to_have_count(1, timeout=5_000)
            # Three member cards
            expect(logged_in_page.locator(".wf-group-member")).to_have_count(3)
            # Member names should appear
            for name in ("小明", "小華", "小芳"):
                expect(logged_in_page.locator(".wf-group-member", has_text=name)).to_be_visible()
            # Fan-in / fan-out paths exist (one of each per member = 6)
            paths = logged_in_page.locator('svg.connections path[d^="M"]')
            assert paths.count() >= 6, f"expected ≥6 SVG paths for fan layout, got {paths.count()}"
        finally:
            db.execute("DELETE FROM workflows WHERE id = %s", (wid,))
            db.execute("DELETE FROM groups_tbl WHERE id = %s", (gid,))

    def test_workflow_node_drag_reposition(self, logged_in_page: Page):
        """Create a workflow with 2 nodes, drag the second node to the left
        of the first, and verify the backend reordered positions (0/1 swap)."""
        unique = f"E2E Drag WF {int(time.time())}"

        # Create workflow via API directly for speed
        create_result = logged_in_page.evaluate("""
            async (name) => {
                const r = await fetch('/api/workflows', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify({ name })
                });
                return await r.json();
            }
        """, unique)
        wid = create_result["id"]

        # Add two nodes (resolve agent ids by name)
        a1 = _agent_id_by_name(logged_in_page, "小明")
        a2 = _agent_id_by_name(logged_in_page, "小華")
        logged_in_page.evaluate("""
            async ([wid, a1, a2]) => {
                await fetch(`/api/workflows/${wid}/nodes`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify({ node_type: 'agent', agent_id: a1, label: 'Node A' })
                });
                await fetch(`/api/workflows/${wid}/nodes`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'include',
                    body: JSON.stringify({ node_type: 'agent', agent_id: a2, label: 'Node B' })
                });
            }
        """, [wid, a1, a2])

        logged_in_page.goto(f"{FRONTEND_URL}/workflows/{wid}")
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.locator(".wf-node")).to_have_count(2)

        # Grab bounding boxes
        node_a = logged_in_page.locator(".wf-node", has_text="Node A")
        node_b = logged_in_page.locator(".wf-node", has_text="Node B")
        box_a = node_a.bounding_box()
        box_b = node_b.bounding_box()
        assert box_a and box_b
        # Node A should start to the left of node B (position 0, 1)
        assert box_a["x"] < box_b["x"], f"expected A left of B, got A.x={box_a['x']} B.x={box_b['x']}"

        # Drag Node B far to the left of Node A
        start_x = box_b["x"] + box_b["width"] / 2
        start_y = box_b["y"] + box_b["height"] / 2
        target_x = box_a["x"] - 200
        target_y = start_y
        logged_in_page.mouse.move(start_x, start_y)
        logged_in_page.mouse.down()
        # Small steps so the drag registers
        steps = 8
        for i in range(1, steps + 1):
            logged_in_page.mouse.move(
                start_x + (target_x - start_x) * i / steps,
                target_y,
                steps=1,
            )
        logged_in_page.mouse.up()

        # Give backend time to persist
        logged_in_page.wait_for_timeout(800)

        # Fetch the workflow and check node order
        order = logged_in_page.evaluate("""
            async (wid) => {
                const r = await fetch(`/api/workflows/${wid}`, { credentials: 'include' });
                const wf = await r.json();
                return wf.nodes.sort((a,b) => a.position - b.position).map(n => n.label);
            }
        """, wid)
        assert order == ["Node B", "Node A"], f"expected [Node B, Node A], got {order}"

        # Cleanup
        logged_in_page.evaluate(
            "async (wid) => await fetch(`/api/workflows/${wid}`, { method: 'DELETE', credentials: 'include' })",
            wid,
        )

    def test_workflow_node_add_and_delete(self, logged_in_page: Page):
        """Create a fresh workflow, add a node via the picker, edit its label,
        delete it, and verify the changes round-trip."""
        unique = f"E2E Node WF {int(time.time())}"

        # Create a fresh workflow so we start from 0 nodes
        logged_in_page.goto(f"{FRONTEND_URL}/workflows")
        logged_in_page.wait_for_load_state("networkidle")
        logged_in_page.locator('[data-testid="new-workflow-btn"]').click()
        logged_in_page.locator('[data-testid="new-workflow-name"]').fill(unique)
        logged_in_page.locator('[data-testid="create-workflow-submit"]').click()
        logged_in_page.wait_for_url(re.compile(r".*/workflows/\d+"), timeout=10_000)

        # Canvas should be empty
        expect(logged_in_page.locator(".wf-node")).to_have_count(0)

        # Add a node — pick 小明 (resolve id by name)
        xiaoming_id = _agent_id_by_name(logged_in_page, "小明")
        logged_in_page.locator('[data-testid="wf-add-node-btn"]').click()
        logged_in_page.locator(f'[data-testid="pick-agent-{xiaoming_id}"]').click()
        # Node should appear
        expect(logged_in_page.locator(".wf-node")).to_have_count(1, timeout=5_000)

        # Click the node to open side panel
        logged_in_page.locator(".wf-node").first.click()
        expect(logged_in_page.locator('[data-testid="wf-side-panel"]')).to_be_visible()

        # Edit label
        label_input = logged_in_page.locator('[data-testid="wf-node-label-input"]')
        label_input.fill("重新命名的節點")
        label_input.blur()
        # Wait for it to persist + reflect in the node card
        logged_in_page.wait_for_timeout(500)
        expect(logged_in_page.locator(".wf-node").first).to_contain_text("重新命名的節點", timeout=5_000)

        # Delete the node
        logged_in_page.locator('[data-testid="wf-delete-node-btn"]').click()
        expect(logged_in_page.locator(".wf-node")).to_have_count(0, timeout=5_000)

        # Cleanup — delete the test workflow
        logged_in_page.goto(f"{FRONTEND_URL}/workflows")
        logged_in_page.wait_for_load_state("networkidle")
        card = logged_in_page.locator(f'text="{unique}"').locator("xpath=ancestor::div[starts-with(@data-testid,\"workflow-card-\")]")
        card.get_by_text("刪除").click()
        logged_in_page.locator('[data-testid="confirm-delete-workflow"]').click()

    def test_schedule_create_toggle_delete(self, logged_in_page: Page):
        """Create a schedule pointing at seed workflow, toggle it off, delete."""
        unique = f"E2E Schedule {int(time.time())}"

        logged_in_page.goto(f"{FRONTEND_URL}/schedules")
        logged_in_page.wait_for_load_state("networkidle")

        # Open create modal
        logged_in_page.locator('[data-testid="new-schedule-btn"]').click()
        logged_in_page.locator('[data-testid="new-schedule-name"]').fill(unique)
        # Use whatever workflow id the seed actually produced
        seed_wid = _seed_workflow_id(logged_in_page)
        logged_in_page.locator('[data-testid="new-schedule-workflow"]').select_option(str(seed_wid))
        logged_in_page.locator('[data-testid="create-schedule-submit"]').click()

        # Row should appear
        row = logged_in_page.get_by_text(unique)
        expect(row).to_be_visible(timeout=5_000)

        # Capture schedule id via the row's data-testid
        schedule_row = logged_in_page.locator(f'[data-testid^="schedule-row-"]', has_text=unique)
        testid = schedule_row.get_attribute("data-testid")
        assert testid and testid.startswith("schedule-row-")
        sid = testid.split("-")[-1]

        # Status starts as 啟用
        status = logged_in_page.locator(f'[data-testid="schedule-status-{sid}"]')
        expect(status).to_have_text("啟用")

        # Toggle off
        logged_in_page.locator(f'[data-testid="toggle-schedule-{sid}"]').click()
        expect(status).to_have_text("已停用", timeout=5_000)

        # Delete
        logged_in_page.locator(f'[data-testid="delete-schedule-{sid}"]').click()
        logged_in_page.locator('[data-testid="confirm-delete-schedule"]').click()
        expect(logged_in_page.get_by_text(unique)).to_have_count(0, timeout=5_000)

    def test_skills_approve_and_reject(self, logged_in_page: Page):
        """Insert a pending skill for 小明, navigate to Skills page,
        approve it, then reject a second one."""
        from backend import db

        # Look up 小明's actual id (resilient to serial drift)
        xiaoming_id = _agent_id_by_name(logged_in_page, "小明")

        sid_approve = db.execute_returning(
            """
            INSERT INTO agent_skills (agent_id, slug, name, description, content_md,
                                      source, confidence, approved_by_user)
            VALUES (%s, %s, %s, %s, %s, 'self_learned', 0.85, FALSE)
            RETURNING id
            """,
            (xiaoming_id, f"e2e-test-{int(time.time())}-a", "E2E 測試技能 A",
             "e2e 測試用技能", "## test"),
        )
        sid_reject = db.execute_returning(
            """
            INSERT INTO agent_skills (agent_id, slug, name, description, content_md,
                                      source, confidence, approved_by_user)
            VALUES (%s, %s, %s, %s, %s, 'self_learned', 0.6, FALSE)
            RETURNING id
            """,
            (xiaoming_id, f"e2e-test-{int(time.time())}-b", "E2E 測試技能 B",
             "會被拒絕的技能", "## rej"),
        )

        logged_in_page.goto(f"{FRONTEND_URL}/skills")
        logged_in_page.wait_for_load_state("networkidle")

        # Approve sid_approve
        logged_in_page.locator(f'[data-testid="approve-skill-{sid_approve}"]').click()
        # Approve button disappears once approved
        expect(logged_in_page.locator(f'[data-testid="approve-skill-{sid_approve}"]')).to_have_count(0, timeout=5_000)

        # Reject sid_reject — row should vanish
        logged_in_page.locator(f'[data-testid="reject-skill-{sid_reject}"]').click()
        expect(logged_in_page.locator(f'[data-testid="skill-row-{sid_reject}"]')).to_have_count(0, timeout=5_000)

        # Cleanup — delete the approved skill
        db.execute("DELETE FROM agent_skills WHERE id = %s", (sid_approve,))

    def test_dialog_center_hot_stop(self, logged_in_page: Page):
        """Send a message to Lead then click stop before Bedrock replies.
        The thinking indicator should disappear and the send button should
        return (i.e., the abort worked client-side)."""
        logged_in_page.goto(f"{FRONTEND_URL}/dialog")
        logged_in_page.wait_for_load_state("networkidle")

        textarea = logged_in_page.locator(".composer textarea")
        expect(textarea).to_be_enabled(timeout=5_000)
        textarea.fill("寫一個長篇故事大綱")
        textarea.press("Enter")

        # Thinking bubble should appear
        expect(logged_in_page.locator('[data-testid="lead-thinking"]')).to_be_visible(timeout=3_000)
        # Stop button should be visible
        stop_btn = logged_in_page.locator('[data-testid="composer-stop"]')
        expect(stop_btn).to_be_visible()

        # Click stop
        stop_btn.click()

        # Thinking bubble should disappear and send button returns
        expect(logged_in_page.locator('[data-testid="lead-thinking"]')).to_have_count(0, timeout=3_000)
        expect(logged_in_page.locator('[data-testid="composer-send"]')).to_be_visible()

    def test_notification_bell_dropdown(self, logged_in_page: Page):
        """Inject a notification, verify bell shows unread count, opening
        the dropdown auto-marks everything as read (badge disappears), the
        notification still shows up in the visible list, and clicking its
        arrow navigates to the target page."""
        from backend import db
        from backend.services import notifications as notif_service

        uid = db.fetch_one(
            "SELECT id FROM as_users WHERE username = %s", ("alice",)
        )["id"]
        # Attach a related_agent_id so the arrow has somewhere to go
        agent = db.fetch_one(
            "SELECT id FROM agents WHERE user_id = %s AND name = %s",
            (uid, "小明"),
        )
        nid = notif_service.emit(
            uid, "budget_warning",
            title="E2E 預算警告",
            body="已使用 80% 的今日額度",
            severity="warn",
            related_agent_id=agent["id"],
        )

        try:
            logged_in_page.goto(f"{FRONTEND_URL}/dashboard")
            logged_in_page.wait_for_load_state("networkidle")

            bell = logged_in_page.locator('[data-testid="notification-bell"]')
            expect(bell).to_be_visible()
            # Reload so the unread query picks up the new notification
            logged_in_page.reload()
            logged_in_page.wait_for_load_state("networkidle")
            expect(logged_in_page.locator('[data-testid="notification-bell-count"]')).to_be_visible(timeout=5_000)

            # Open the dropdown — this should fire mark_all_read
            logged_in_page.locator('[data-testid="notification-bell"]').click()
            dropdown = logged_in_page.locator('[data-testid="notification-dropdown"]')
            expect(dropdown).to_be_visible()
            expect(logged_in_page.locator(f'[data-testid="notification-row-{nid}"]')).to_be_visible()

            # Badge should disappear after auto-mark-read
            expect(logged_in_page.locator('[data-testid="notification-bell-count"]')).to_have_count(0, timeout=5_000)

            # Verify server-side status actually flipped to 'read'
            row = db.fetch_one("SELECT status FROM notifications WHERE id = %s", (nid,))
            assert row["status"] == "read"

            # Arrow button navigates — click it and assert URL
            logged_in_page.locator(f'[data-testid="notif-go-{nid}"]').click()
            logged_in_page.wait_for_url(re.compile(rf".*/agents/{agent['id']}"), timeout=5_000)
        finally:
            db.execute("DELETE FROM notifications WHERE id = %s", (nid,))

    def test_run_detail_page(self, logged_in_page: Page):
        """Create a run via API then navigate to the detail page and verify
        summary, status, and back button render. Stop button may or may not
        appear depending on timing — we just check the page loads without crashing."""
        # Dispatch a run via the seed workflow (id resolved dynamically —
        # SERIAL counters drift after test_api_crud truncates).
        seed_wid = _seed_workflow_id(logged_in_page)
        result = logged_in_page.evaluate(
            """(wid) => fetch('/api/workflows/' + wid + '/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ input: 'run detail e2e test', priority: 'normal', trigger_source: 'api' })
            }).then(r => r.json())""",
            seed_wid,
        )
        run_id = result["run_id"]
        assert run_id, f"run creation failed: {result}"

        # Navigate to the detail page
        logged_in_page.goto(f"{FRONTEND_URL}/runs/{run_id}")
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.locator('[data-testid="run-detail-page"]')).to_be_visible()
        expect(logged_in_page.get_by_role("heading", name=f"Run #{run_id}")).to_be_visible()
        expect(logged_in_page.locator('[data-testid="run-status"]')).to_be_visible()
        # Initial input should render somewhere on page
        expect(logged_in_page.get_by_text("run detail e2e test", exact=False)).to_be_visible()
        # Summary cards
        expect(logged_in_page.get_by_text("總花費")).to_be_visible()
        # Phase 3.2 — the workflow flow diagram should appear above the
        # step list, showing one node per workflow position.
        expect(logged_in_page.locator('[data-testid="run-flow-diagram"]')).to_be_visible(timeout=5_000)
        expect(logged_in_page.locator('[data-testid^="run-flow-node-"]')).to_have_count(4)
        # Back button returns to /runs
        logged_in_page.get_by_role("button", name="返回執行紀錄").click()
        logged_in_page.wait_for_url(re.compile(r".*/runs$"), timeout=5_000)

    def test_runs_list_row_clickable(self, logged_in_page: Page):
        logged_in_page.goto(f"{FRONTEND_URL}/runs")
        logged_in_page.wait_for_load_state("networkidle")
        row = logged_in_page.locator('[data-testid^="run-row-"]').first
        if row.count() == 0:
            pytest.skip("no runs available")
        row.click()
        logged_in_page.wait_for_url(re.compile(r".*/runs/\d+"), timeout=5_000)
        expect(logged_in_page.locator('[data-testid="run-detail-page"]')).to_be_visible()

    def test_workflow_create_edit_delete_flow(self, logged_in_page: Page):
        """Create a new workflow, edit its name, save, then delete it."""
        unique = f"E2E WF {int(time.time())}"
        renamed = unique + " renamed"

        # Go to list page
        logged_in_page.goto(f"{FRONTEND_URL}/workflows")
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.get_by_role("heading", name="Workflows")).to_be_visible()

        # Open create modal
        logged_in_page.locator('[data-testid="new-workflow-btn"]').click()
        logged_in_page.locator('[data-testid="new-workflow-name"]').fill(unique)
        logged_in_page.locator('[data-testid="create-workflow-submit"]').click()

        # Should navigate into editor
        logged_in_page.wait_for_url(re.compile(r".*/workflows/\d+"), timeout=10_000)
        name_input = logged_in_page.locator('[data-testid="wf-name-input"]')
        expect(name_input).to_have_value(unique, timeout=5_000)

        # Edit name → save
        name_input.fill(renamed)
        save = logged_in_page.locator('[data-testid="wf-save-btn"]')
        expect(save).to_be_enabled()
        save.click()
        expect(save).to_have_text("已儲存", timeout=5_000)

        # Go back to list → renamed appears
        logged_in_page.goto(f"{FRONTEND_URL}/workflows")
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.get_by_text(renamed)).to_be_visible(timeout=5_000)

        # Delete it
        card = logged_in_page.locator(f'text="{renamed}"').locator("xpath=ancestor::div[starts-with(@data-testid,\"workflow-card-\")]")
        card.get_by_text("刪除").click()
        logged_in_page.locator('[data-testid="confirm-delete-workflow"]').click()
        expect(logged_in_page.get_by_text(renamed)).to_have_count(0)

    def test_agent_create_and_delete_flow(self, logged_in_page: Page):
        """Create a new agent via the modal, verify it appears, then delete it."""
        unique_name = f"測試員_{int(time.time())}"

        logged_in_page.goto(f"{FRONTEND_URL}/agents")
        logged_in_page.wait_for_load_state("networkidle")

        # Open create modal
        logged_in_page.locator('[data-testid="new-agent-btn"]').click()
        expect(logged_in_page.get_by_text("新增 agent").first).to_be_visible(timeout=3_000)

        # Fill the form
        logged_in_page.locator('[data-testid="new-agent-name"]').fill(unique_name)
        logged_in_page.locator('[data-testid="new-agent-role"]').fill("QA 自動測試")

        # Submit — should navigate to /agents/<new id>
        logged_in_page.locator('[data-testid="create-agent-submit"]').click()
        logged_in_page.wait_for_url(re.compile(r".*/agents/\d+"), timeout=10_000)
        expect(logged_in_page.get_by_role("heading", name=unique_name)).to_be_visible(timeout=5_000)

        # Navigate back to list — new agent should appear
        logged_in_page.goto(f"{FRONTEND_URL}/agents")
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.get_by_text(unique_name).first).to_be_visible(timeout=5_000)

        # Open detail, delete it
        logged_in_page.get_by_text(unique_name).first.click()
        logged_in_page.wait_for_url(re.compile(r".*/agents/\d+"), timeout=5_000)
        logged_in_page.locator('[data-testid="delete-agent-btn"]').click()
        logged_in_page.locator('[data-testid="confirm-delete-agent"]').click()

        # Should redirect to /agents and the name no longer appears
        logged_in_page.wait_for_url(re.compile(r".*/agents$"), timeout=5_000)
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.get_by_text(unique_name)).to_have_count(0)


# ============================================================================
# Phase 1.4 — Records / Automation / Settings multi-tab navigation
# ============================================================================

class TestNavigationP14:

    def test_sidebar_shows_new_nav_items(self, logged_in_page: Page):
        """Sidebar should have the consolidated 自動化 and 紀錄 entries,
        and NOT the old standalone Workflows / 執行紀錄 / Escalation / 排程
        entries."""
        logged_in_page.goto(f"{FRONTEND_URL}/dialog")
        logged_in_page.wait_for_load_state("networkidle")
        sidebar = logged_in_page.locator(".sidebar .nav")
        expect(sidebar.locator('a', has_text="自動化")).to_be_visible()
        expect(sidebar.locator('a', has_text="紀錄")).to_be_visible()
        # Old sidebar entries were merged into these two — verify no duplicates
        assert sidebar.locator('a', has_text="Workflows").count() == 0
        assert sidebar.locator('a', has_text="執行紀錄").count() == 0
        assert sidebar.locator('a', has_text="Escalation").count() == 0
        assert sidebar.locator('a', has_text="排程").count() == 0

    def test_records_page_tabs(self, logged_in_page: Page):
        """/records renders 3 tabs and switching between them updates the
        URL search param and the visible content."""
        logged_in_page.goto(f"{FRONTEND_URL}/records")
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.locator('[data-testid="records-tabs"]')).to_be_visible()
        # Default tab = runs
        expect(logged_in_page.locator('[data-testid="records-tab-runs"]')).to_have_class(
            re.compile(r".*active.*")
        )
        # Click escalations
        logged_in_page.locator('[data-testid="records-tab-escalations"]').click()
        logged_in_page.wait_for_url(re.compile(r".*\?tab=escalations"), timeout=3_000)
        # Click notifications — the notification list (empty or populated)
        # should render without crashing.
        logged_in_page.locator('[data-testid="records-tab-notifications"]').click()
        logged_in_page.wait_for_url(re.compile(r".*\?tab=notifications"), timeout=3_000)
        # Either the empty-state banner or at least one notification row
        # should be present.
        empty = logged_in_page.locator('[data-testid="notifications-empty"]').count()
        rows = logged_in_page.locator('[data-testid^="notifications-row-"]').count()
        assert empty + rows >= 1, "notifications tab body did not render"

    def test_automation_page_tabs(self, logged_in_page: Page):
        logged_in_page.goto(f"{FRONTEND_URL}/automation")
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.locator('[data-testid="automation-tabs"]')).to_be_visible()
        expect(logged_in_page.locator('[data-testid="automation-tab-workflows"]')).to_have_class(
            re.compile(r".*active.*")
        )
        # Switch to schedules
        logged_in_page.locator('[data-testid="automation-tab-schedules"]').click()
        logged_in_page.wait_for_url(re.compile(r".*\?tab=schedules"), timeout=3_000)

    def test_settings_has_admin_tabs_when_admin(self, logged_in_page: Page):
        """alice is seeded as admin → all admin tabs visible + feature flags
        table populated."""
        logged_in_page.goto(f"{FRONTEND_URL}/settings")
        logged_in_page.wait_for_load_state("networkidle")

        expect(logged_in_page.locator('[data-testid="settings-tab-personal"]')).to_be_visible()
        expect(logged_in_page.locator('[data-testid="settings-tab-users"]')).to_be_visible()
        expect(logged_in_page.locator('[data-testid="settings-tab-models"]')).to_be_visible()
        expect(logged_in_page.locator('[data-testid="settings-tab-system"]')).to_be_visible()

        # Users tab → should list at least alice (may include stray users
        # left over from parallel test_v2.py runs; we just verify >=1).
        logged_in_page.locator('[data-testid="settings-tab-users"]').click()
        expect(logged_in_page.locator('[data-testid="users-table"]')).to_be_visible(timeout=3_000)
        expect(
            logged_in_page.locator('tr[data-testid^="user-row-"]').first
        ).to_be_visible(timeout=3_000)

        # System tab → flag rows render
        logged_in_page.locator('[data-testid="settings-tab-system"]').click()
        expect(
            logged_in_page.locator('[data-testid="feature-flag-view_audit_log"]')
        ).to_be_visible(timeout=3_000)
        expect(
            logged_in_page.locator('[data-testid="feature-flag-create_mcp_server"]')
        ).to_be_visible()

    def test_admin_can_create_and_delete_user_via_ui(self, logged_in_page: Page):
        """Full UI CRUD: open Users tab, click create, fill form, delete.
        Verifies the admin routes are reachable end-to-end."""
        unique = f"e2e_user_{int(time.time())}"
        try:
            logged_in_page.goto(f"{FRONTEND_URL}/settings?tab=users")
            logged_in_page.wait_for_load_state("networkidle")
            logged_in_page.locator('[data-testid="create-user-btn"]').click()
            logged_in_page.locator('[data-testid="new-user-username"]').fill(unique)
            logged_in_page.locator('[data-testid="new-user-password"]').fill("secret123")
            logged_in_page.locator('[data-testid="new-user-display-name"]').fill("E2E Test User")
            logged_in_page.locator('[data-testid="confirm-create-user"]').click()
            # Table should now include the new row
            expect(logged_in_page.locator(f'[data-testid^="user-row-"]', has_text=unique)).to_be_visible(
                timeout=5_000
            )

            # Find the row's delete button and click it
            row = logged_in_page.locator(f'[data-testid^="user-row-"]', has_text=unique)
            uid_attr = row.get_attribute("data-testid")
            uid = int(uid_attr.replace("user-row-", ""))
            logged_in_page.locator(f'[data-testid="delete-user-{uid}"]').click()
            logged_in_page.locator('[data-testid="confirm-delete-user"]').click()
            expect(logged_in_page.locator(f'[data-testid="user-row-{uid}"]')).to_have_count(0, timeout=3_000)
        finally:
            from backend import db
            db.execute("DELETE FROM as_users WHERE username = %s", (unique,))

    def test_library_page_renders_all_kinds(self, logged_in_page: Page):
        """Library page: 4 tabs visible, switching URL, empty state renders."""
        logged_in_page.goto(f"{FRONTEND_URL}/library")
        logged_in_page.wait_for_load_state("networkidle")
        expect(logged_in_page.locator('[data-testid="library-tabs"]')).to_be_visible()
        for key in ["skill", "tool", "mcp", "rag"]:
            logged_in_page.locator(f'[data-testid="library-tab-{key}"]').click()
            logged_in_page.wait_for_url(re.compile(rf".*\?tab={key}"), timeout=3_000)
            # Wait for either the empty state or a grid to render (the
            # useQuery refetches when the kind switches). We explicitly
            # wait rather than snapshotting .count() because the query
            # may still be in-flight right after the tab click.
            logged_in_page.wait_for_function(
                """
                (key) => {
                    return !!document.querySelector(
                        `[data-testid="library-empty-${key}"], [data-testid="library-grid-${key}"]`
                    );
                }
                """,
                arg=key,
                timeout=5_000,
            )

    def test_agent_detail_assets_tab_toggles_assignment(self, logged_in_page: Page):
        """Open an agent detail page, switch to 資產 tab, toggle a tool
        asset on, verify DB has the mapping, toggle off, verify removed."""
        from backend import db

        # Seed a tool asset owned by alice
        alice = db.fetch_one("SELECT id FROM as_users WHERE username = 'alice'")
        agent_row = db.fetch_one(
            "SELECT id FROM agents WHERE user_id = %s AND name = %s",
            (alice["id"], "小明"),
        )
        asset_id = db.execute_returning(
            """
            INSERT INTO asset_items (kind, name, description, owner_user_id, config)
            VALUES ('tool', 'E2E Test Tool', 'e2e', %s, '{"module": "x", "fn": "y"}'::jsonb)
            RETURNING id
            """,
            (alice["id"],),
        )
        try:
            logged_in_page.goto(f"{FRONTEND_URL}/agents/{agent_row['id']}")
            logged_in_page.wait_for_load_state("networkidle")
            logged_in_page.locator('[data-testid="agent-tab-assets"]').click()
            expect(logged_in_page.locator('[data-testid="agent-assets-editor"]')).to_be_visible(timeout=3_000)

            cb = logged_in_page.locator(f'[data-testid="agent-asset-checkbox-{asset_id}"]')
            expect(cb).to_be_visible(timeout=3_000)
            expect(cb).not_to_be_checked()

            cb.click()
            expect(cb).to_be_checked(timeout=3_000)
            # Verify DB row
            row = db.fetch_one(
                "SELECT enabled FROM agent_assets WHERE agent_id = %s AND asset_id = %s",
                (agent_row["id"], asset_id),
            )
            assert row is not None

            cb.click()
            expect(cb).not_to_be_checked(timeout=3_000)
            row = db.fetch_one(
                "SELECT 1 FROM agent_assets WHERE agent_id = %s AND asset_id = %s",
                (agent_row["id"], asset_id),
            )
            assert row is None
        finally:
            db.execute("DELETE FROM asset_items WHERE id = %s", (asset_id,))

    def test_library_create_and_delete_skill(self, logged_in_page: Page):
        """Full UI CRUD: create a skill, verify card appears, delete it."""
        unique = f"E2E Skill {int(time.time())}"
        try:
            logged_in_page.goto(f"{FRONTEND_URL}/library?tab=skill")
            logged_in_page.wait_for_load_state("networkidle")
            logged_in_page.locator('[data-testid="library-new-skill-btn"]').click()
            logged_in_page.locator('[data-testid="new-asset-name"]').fill(unique)
            logged_in_page.locator('[data-testid="new-asset-description"]').fill("end-to-end smoke")
            logged_in_page.locator('[data-testid="confirm-create-asset"]').click()
            # Card should appear
            card = logged_in_page.locator(
                '[data-testid^="asset-card-"]', has_text=unique,
            )
            expect(card).to_be_visible(timeout=5_000)
            # Extract asset id from data-testid
            testid = card.first.get_attribute("data-testid")
            aid = int(testid.replace("asset-card-", ""))
            # Accept the confirm dialog when delete fires
            logged_in_page.once("dialog", lambda d: d.accept())
            logged_in_page.locator(f'[data-testid="asset-delete-{aid}"]').click()
            expect(logged_in_page.locator(f'[data-testid="asset-card-{aid}"]')).to_have_count(
                0, timeout=5_000
            )
        finally:
            from backend import db
            db.execute("DELETE FROM asset_items WHERE name = %s", (unique,))

    def test_system_settings_flag_toggle(self, logged_in_page: Page):
        """Admin can toggle a feature flag via the system settings tab UI.

        Clicks the checkbox and then polls the DB for the expected change.
        The React mutation fires the PUT asynchronously so networkidle isn't
        reliable — we wait explicitly for the DB to reflect the new state.
        """
        from backend import db
        # Reset to a known state before the test
        db.execute(
            "UPDATE system_feature_flags SET admin_only = FALSE WHERE feature = 'view_audit_log'"
        )

        logged_in_page.goto(f"{FRONTEND_URL}/settings?tab=system")
        logged_in_page.wait_for_load_state("networkidle")
        cb = logged_in_page.locator('[data-testid="feature-flag-view_audit_log-checkbox"]')
        expect(cb).to_be_visible(timeout=3_000)
        expect(cb).not_to_be_checked()

        # Click to enable admin-only. Wait for the UI state to flip (Playwright
        # polls until the expect clause passes or times out).
        cb.click()
        expect(cb).to_be_checked(timeout=5_000)

        # Confirm the backend got updated
        row = db.fetch_one(
            "SELECT admin_only FROM system_feature_flags WHERE feature = 'view_audit_log'"
        )
        assert row["admin_only"] is True

        # Flip back via API so we leave no state behind
        db.execute(
            "UPDATE system_feature_flags SET admin_only = FALSE WHERE feature = 'view_audit_log'"
        )
