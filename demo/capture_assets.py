"""Playwright-driven walkthrough that produces README screenshots + demo videos.

Prereqs:
  * Backend running on 8087 (Postgres or SQLite).
  * Frontend dev server on 5173 (`cd frontend && npm run dev`).
  * Demo data seeded: `python -m demo.seed_demo`  (user jay / demo).
  * `pip install playwright && python -m playwright install chromium`

Run from repo root:
  python -m demo.capture_assets                  # all scenes
  python -m demo.capture_assets --scene groupchat # one scene

Each scene produces its own screenshots + a focused mp4/webm, so the
README can embed short clips instead of one long reel. A combined
walkthrough video is also produced as `demo-walkthrough.webm`.

Output tree:
  docs/assets/screenshots/
      01-login.png
      02-dialog.png
      03-groups.png
      04-group-chat.png
      05-workflows.png
      06-workflow-editor.png
      07-dashboard.png
      08-library.png
  docs/assets/videos/
      01-dialog-workflow-proposal.webm
      02-group-chat.webm
      03-dashboard.webm
      04-library.webm
  docs/assets/demo-walkthrough.webm
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright


BASE_URL = os.environ.get("AC_DEMO_URL", "http://localhost:5173")
USERNAME = os.environ.get("AC_DEMO_USER", "jay")
PASSWORD = os.environ.get("AC_DEMO_PASS", "demo")

REPO_ROOT = Path(__file__).resolve().parent.parent
SHOTS_DIR = REPO_ROOT / "docs" / "assets" / "screenshots"
VIDEOS_DIR = REPO_ROOT / "docs" / "assets" / "videos"
TOP_ASSETS = REPO_ROOT / "docs" / "assets"
SHOTS_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
TOP_ASSETS.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settle(page: Page, ms: int = 800) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=5_000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def _shot(page: Page, name: str) -> None:
    out = SHOTS_DIR / name
    page.screenshot(path=str(out), full_page=False)
    print(f"  screenshot: {out.relative_to(REPO_ROOT)}")


def _login(page: Page) -> None:
    page.goto(f"{BASE_URL}/login")
    _settle(page)
    page.locator(
        'input[name="username"], input[placeholder*="user" i], input[type="text"]'
    ).first.fill(USERNAME)
    page.locator('input[type="password"]').first.fill(PASSWORD)
    page.keyboard.press("Enter")
    _settle(page, ms=1500)


def _promote_video(context: BrowserContext, target: Path) -> Path | None:
    """Close context so playwright flushes the video, then rename the webm."""
    context.close()
    # Playwright writes one random-named .webm per page — grab the newest.
    candidates = [p for p in VIDEOS_DIR.glob("*.webm") if p.name != target.name]
    candidates += [p for p in TOP_ASSETS.glob("*.webm") if p.is_file()
                   and not p.name.startswith("demo-")
                   and p.parent != VIDEOS_DIR]
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    if target.exists():
        target.unlink()
    newest.rename(target)
    print(f"  video: {target.relative_to(REPO_ROOT)}")
    return target


def _maybe_mp4(webm: Path) -> Path | None:
    if not shutil.which("ffmpeg"):
        return None
    mp4 = webm.with_suffix(".mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(webm),
         "-c:v", "libx264", "-crf", "22", "-preset", "medium",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         str(mp4)],
        check=False,
        capture_output=True,
    )
    if mp4.exists():
        print(f"  mp4: {mp4.relative_to(REPO_ROOT)}")
        return mp4
    return None


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

def scene_login_dialog(pw):
    """Login + dialog landing + a workflow-probing chat message."""
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800},
        record_video_dir=str(VIDEOS_DIR),
        record_video_size={"width": 1280, "height": 800},
    )
    page = ctx.new_page()
    _login(page)
    _shot(page, "01-login.png")

    page.goto(f"{BASE_URL}/dialog")
    _settle(page, ms=1500)
    _shot(page, "02-dialog.png")

    # Try to elicit a workflow proposal from Lead (non-deterministic; the
    # screenshot is still fine as a "chat in progress" shot even if the LLM
    # doesn't emit a workflow block).
    prompt = (
        "I want to spin up a pitch for a B2B AI assistant for accountants. "
        "Can you sketch a workflow where three founders each propose an "
        "angle, three VCs critique them, and you produce a final markdown "
        "pitch deck?"
    )
    textarea = page.locator("textarea").first
    if textarea.count() > 0:
        textarea.fill(prompt)
        page.keyboard.press("Enter")
        # Lead responses with a workflow block take ~20-35s. Wait longer.
        page.wait_for_timeout(35_000)

        # Scroll the Suggested Workflow card into view — it's the
        # promo-worthy part of the response and we want it visible in the
        # screenshot used for the README hero. The card is rendered by
        # WorkflowBubble with a top label `.wf-bubble-label`.
        card = page.locator(".wf-bubble").first
        if card.count() > 0:
            try:
                card.scroll_into_view_if_needed(timeout=3000)
                # Center the card in the viewport so we can see the label
                # at the top AND the body below without clipping.
                page.evaluate(
                    """
                    const el = document.querySelector('.wf-bubble');
                    if (el) el.scrollIntoView({block: 'center', behavior: 'instant'});
                    """
                )
            except Exception:
                pass
        page.wait_for_timeout(800)
        _shot(page, "02b-dialog-lead-response.png")

    _promote_video(ctx, VIDEOS_DIR / "01-dialog-workflow-proposal.webm")
    browser.close()


def scene_group_chat(pw):
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800},
        record_video_dir=str(VIDEOS_DIR),
        record_video_size={"width": 1280, "height": 800},
    )
    page = ctx.new_page()
    _login(page)

    page.goto(f"{BASE_URL}/groups")
    _settle(page, ms=1000)
    _shot(page, "03-groups.png")

    chat_btn = page.locator('button:has-text("Open chat")').first
    if chat_btn.count() > 0:
        chat_btn.click()
        _settle(page, ms=1500)
        _shot(page, "04-group-chat.png")

        # Send a real discussion prompt + let the replies stream in.
        textarea = page.locator("textarea").first
        textarea.fill(
            "Let's brainstorm: a small-town pharmacist finds out her late "
            "father was running an underground clinic. Where do we start?"
        )
        page.keyboard.press("Enter")
        # Writers Room is sequential (3 members) — roughly 30–60s total.
        page.wait_for_timeout(40_000)
        _shot(page, "04b-group-chat-active.png")

    _promote_video(ctx, VIDEOS_DIR / "02-group-chat.webm")
    browser.close()


def scene_workflows(pw):
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800},
        record_video_dir=str(VIDEOS_DIR),
        record_video_size={"width": 1280, "height": 800},
    )
    page = ctx.new_page()
    _login(page)

    page.goto(f"{BASE_URL}/workflows")
    _settle(page, ms=1000)
    _shot(page, "05-workflows.png")

    # Open the Pitch Deck workflow in the editor.
    # The Pitch Deck card has an "Edit" button that routes to the editor.
    # Target it relative to a card that says "Pitch Deck" so we don't click
    # the wrong one.
    pitch_card = page.locator('div:has-text("Pitch Deck — 3 rounds")').last
    edit_btn = pitch_card.locator('button:has-text("Edit")').first
    if edit_btn.count() == 0:
        edit_btn = page.locator('button:has-text("Edit")').first
    if edit_btn.count() > 0:
        edit_btn.click()
        _settle(page, ms=2500)
        _shot(page, "06-workflow-editor.png")

    _promote_video(ctx, VIDEOS_DIR / "03-workflow-editor.webm")
    browser.close()


def scene_dashboard(pw):
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800},
        record_video_dir=str(VIDEOS_DIR),
        record_video_size={"width": 1280, "height": 800},
    )
    page = ctx.new_page()
    _login(page)

    page.goto(f"{BASE_URL}/dashboard")
    _settle(page, ms=1500)
    _shot(page, "07-dashboard.png")

    _promote_video(ctx, VIDEOS_DIR / "04-dashboard.webm")
    browser.close()


def scene_library(pw):
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800},
        record_video_dir=str(VIDEOS_DIR),
        record_video_size={"width": 1280, "height": 800},
    )
    page = ctx.new_page()
    _login(page)

    page.goto(f"{BASE_URL}/library")
    _settle(page, ms=1500)
    _shot(page, "08-library.png")

    # Click through the kind tabs if visible so the video shows each surface.
    for label in ("Skill", "Tool", "MCP"):
        tab = page.locator(f'button:has-text("{label}")').first
        if tab.count() > 0:
            tab.click()
            _settle(page, ms=800)

    _promote_video(ctx, VIDEOS_DIR / "05-library.webm")
    browser.close()


def scene_walkthrough(pw):
    """One combined promo reel covering the five scenes end-to-end."""
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800},
        record_video_dir=str(TOP_ASSETS),
        record_video_size={"width": 1280, "height": 800},
    )
    page = ctx.new_page()
    _login(page)

    page.goto(f"{BASE_URL}/dialog");     _settle(page, ms=2500)
    page.goto(f"{BASE_URL}/groups");     _settle(page, ms=2000)
    chat_btn = page.locator('button:has-text("Open chat")').first
    if chat_btn.count() > 0:
        chat_btn.click();                 _settle(page, ms=2500)
    page.goto(f"{BASE_URL}/workflows");  _settle(page, ms=2000)
    page.goto(f"{BASE_URL}/dashboard");  _settle(page, ms=2500)
    page.goto(f"{BASE_URL}/library");    _settle(page, ms=2000)

    _promote_video(ctx, TOP_ASSETS / "demo-walkthrough.webm")
    browser.close()


ALL_SCENES = {
    "login": scene_login_dialog,
    "groupchat": scene_group_chat,
    "workflows": scene_workflows,
    "dashboard": scene_dashboard,
    "library": scene_library,
    "walkthrough": scene_walkthrough,
}


def run(scenes: list[str]):
    print(f"Recording against {BASE_URL} as {USERNAME}/…")
    with sync_playwright() as pw:
        for name in scenes:
            fn = ALL_SCENES.get(name)
            if not fn:
                print(f"  (skipping unknown scene: {name})")
                continue
            print(f"\n· scene: {name}")
            fn(pw)

    # Post-process: convert every webm to mp4 if ffmpeg is present.
    all_webms = list(VIDEOS_DIR.glob("*.webm")) + list(TOP_ASSETS.glob("demo-*.webm"))
    if shutil.which("ffmpeg"):
        print("\nConverting webm → mp4…")
        for w in all_webms:
            _maybe_mp4(w)
    else:
        print("\nffmpeg not on PATH — keeping webm only.")
    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scene",
        action="append",
        choices=list(ALL_SCENES) + ["all"],
        default=None,
        help="Record a specific scene (repeatable). Default: all.",
    )
    args = parser.parse_args()
    scenes = args.scene or ["all"]
    if "all" in scenes:
        scenes = list(ALL_SCENES)
    try:
        run(scenes)
    except KeyboardInterrupt:
        sys.exit(130)
