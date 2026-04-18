"""Playwright audit — visit every UI route, capture screenshots, collect
console + network errors. Produces a per-page report so we can spot broken
screens without clicking through manually.

Prereqs (same as capture_assets): backend on 8087, frontend on 5173,
jay/demo seeded.

Run:
    python -m demo.audit_ui
"""
from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


BASE = os.environ.get("AC_DEMO_URL", "http://localhost:5173")
OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets" / "audit"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# (slug, path). Dynamic ids resolved via known rows.
STATIC_ROUTES = [
    ("dialog",       "/dialog"),
    ("dashboard",    "/dashboard"),
    ("agents",       "/agents"),
    ("groups",       "/groups"),
    ("projects",     "/projects"),
    ("automation",   "/automation"),
    ("records",      "/records"),
    ("workflows",    "/workflows"),
    ("escalations",  "/escalations"),
    ("runs",         "/runs"),
    ("schedules",    "/schedules"),
    ("skills",       "/skills"),
    ("library",      "/library"),
    ("settings",     "/settings"),
]


def _login(page: Page):
    page.goto(f"{BASE}/login")
    page.wait_for_load_state("networkidle", timeout=5000)
    page.locator('input[type="text"], input[name="username"]').first.fill("jay")
    page.locator('input[type="password"]').first.fill("demo")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)


def _first_existing(page: Page, paths: list[str]) -> int | None:
    """Hit each list route, pick the first URL that yields data via the
    first-row link. For /agents, click the first row. Returns the id
    observed in the URL after click."""
    for p in paths:
        page.goto(f"{BASE}{p}")
        page.wait_for_timeout(800)
    return None


def _audit_page(page: Page, slug: str, url: str) -> dict:
    console_msgs: list[str] = []
    network_fails: list[str] = []

    def on_console(m):
        if m.type in ("error", "warning"):
            console_msgs.append(f"[{m.type}] {m.text[:400]}")

    def on_response(r):
        if not r.ok:
            if r.status == 304 or r.status == 0:
                return
            # Skip images / static 404s that aren't actionable here
            if r.url.endswith((".png", ".jpg", ".svg", ".ico", ".webp")):
                return
            network_fails.append(f"{r.status} {r.url}")

    page.on("console", on_console)
    page.on("response", on_response)

    try:
        page.goto(f"{BASE}{url}")
    except Exception as e:
        return {"slug": slug, "url": url, "crashed": str(e),
                "console": [], "network": []}

    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass
    page.wait_for_timeout(1000)

    # Screenshot
    shot = OUT_DIR / f"{slug}.png"
    try:
        page.screenshot(path=str(shot), full_page=False)
    except Exception as e:
        return {"slug": slug, "url": url, "crashed": str(e),
                "console": console_msgs, "network": network_fails}

    # A rough "page looks broken" heuristic: body's rendered text length.
    body_text_len = page.evaluate("() => document.body.innerText.length")

    page.remove_listener("console", on_console)
    page.remove_listener("response", on_response)

    return {
        "slug": slug, "url": url,
        "screenshot": str(shot.relative_to(OUT_DIR.parent.parent)),
        "text_len": body_text_len,
        "console": console_msgs,
        "network": network_fails,
    }


def run():
    reports = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        _login(page)

        # Static routes first
        for slug, path in STATIC_ROUTES:
            print(f"· {slug:12} {path}")
            reports.append(_audit_page(page, slug, path))

        # Dynamic: pick the first project/agent/workflow row we can see.
        # Use the API to get live ids.
        import urllib.request, json
        def _api(path):
            req = urllib.request.Request(f"http://localhost:8087{path}",
                                         headers={"Cookie": f"session={page.context.cookies()[0]['value']}"
                                                  if page.context.cookies() else ""})
            try:
                with urllib.request.urlopen(req) as r:
                    return json.loads(r.read())
            except Exception:
                return None

        # Cookie plumbing for API is annoying; instead use playwright's own
        # request() API which shares the session cookie.
        ctx_req = ctx.request
        for listing, slug_prefix, detail_path in [
            ("/api/projects",  "project-detail",  "/projects"),
            ("/api/agents",    "agent-detail",    "/agents"),
            ("/api/workflows", "workflow-detail", "/workflows"),
            ("/api/runs?limit=1", "run-detail",   "/runs"),
        ]:
            try:
                r = ctx_req.get(f"{BASE}{listing}")
                if not r.ok:
                    continue
                data = r.json()
                if isinstance(data, dict) and "runs" in data:
                    data = data["runs"]
                if not data:
                    continue
                first_id = data[0]["id"]
                print(f"· {slug_prefix:16} {detail_path}/{first_id}")
                reports.append(_audit_page(
                    page, slug_prefix, f"{detail_path}/{first_id}"
                ))
            except Exception as e:
                print(f"  skip {slug_prefix}: {e}")

        browser.close()

    # Print a compact report.
    print("\n" + "=" * 72)
    print("AUDIT SUMMARY")
    print("=" * 72)
    clean = 0
    for r in reports:
        if r.get("crashed"):
            print(f"💥 {r['slug']:18} {r['url']} — crashed: {r['crashed'][:80]}")
            continue
        issues = []
        if r["text_len"] < 200:
            issues.append(f"low text ({r['text_len']})")
        if r["console"]:
            issues.append(f"{len(r['console'])} console err")
        if r["network"]:
            issues.append(f"{len(r['network'])} net err")
        status = "✅" if not issues else "⚠️ "
        print(f"{status} {r['slug']:18} {r['url']:30} {', '.join(issues) if issues else 'clean'}")
        if not issues:
            clean += 1

    print(f"\n{clean}/{len(reports)} pages clean.\n")
    # Detailed dump of anything with issues
    for r in reports:
        if r.get("crashed") or r.get("console") or r.get("network"):
            print("-" * 72)
            print(f"{r['slug']}  ({r['url']})")
            for m in r.get("console", []):
                print(f"  console: {m}")
            for n in r.get("network", []):
                print(f"  net:     {n}")

    print("\nScreenshots → docs/assets/audit/")


if __name__ == "__main__":
    run()
