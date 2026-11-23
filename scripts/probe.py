"""CP0 live DOM probe. Run: uv run --with "playwright==1.61.*" scripts/probe.py
Output goes to HOME/probe/ (mode 700), never the repo. Exit 3 = not signed in."""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright

from gemini_web import web

# Guesses; CP0 exists to replace these with real ones. Each value is a list of CSS candidates.
CAND = {
    "avatar": ['[aria-label*="Google Account"]', 'a[href*="accounts.google.com/SignOutOptions"]'],
    "textbox": ['div[role="textbox"]', 'rich-textarea div[contenteditable]'],
    "temp_chat": ['[aria-label*="Temporary chat"]', '[aria-label*="temporary" i]'],
    "mode_picker": ['bard-mode-switcher button', '[data-test-id="bard-mode-menu-button"]', '[aria-label*="mode" i]'],
    "mode_menu_items": ['[role="menuitem"]', '[role="menuitemradio"]', 'button.bard-mode-list-button'],
    "stop": ['[aria-label*="Stop" i]'],
    "response": ['model-response'],
    "message_content": ['model-response message-content', '.model-response-text', '.markdown'],
    "image": ['model-response img', 'generated-image img', 'single-image img'],
    "download_full": ['[aria-label*="Download full size" i]', '[aria-label*="Download" i]'],
}
STATE = {"snaps": []}


def grab(page, tag):
    """outerHTML (first 2000 chars) of the first match of each key, or None; plus a screenshot."""
    out = {}
    for key, sels in CAND.items():
        out[key] = None
        for s in sels:
            try:
                loc = page.locator(s)
                if loc.count():
                    out[key] = {"selector": s, "count": loc.count(),
                                "html": loc.first.evaluate("e => e.outerHTML")[:2000]}
                    break
            except Exception as e:
                out[key] = {"selector": s, "error": str(e)[:200]}
    STATE["snaps"].append({"tag": tag, "url": page.url, "keys": out})
    page.screenshot(path=str(web.HOME / "probe" / f"{tag}.png"))
    json.dump(STATE, open(web.HOME / "probe" / "probe.json", "w"), indent=1)
    return out


def visible(page, sel):
    try:
        return page.locator(sel).first.is_visible()
    except Exception:
        return False


def main():
    lock = web.acquire_lock()  # noqa: F841 (held until exit)
    (web.HOME / "probe").mkdir(exist_ok=True)
    (web.HOME / "probe").chmod(0o700)
    kw = web.launch_kwargs()  # may die(1) "Chrome not found": keep it outside the Playwright context
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(str(web.PROFILE), **kw)
            downloads = []
            ctx.on("download", lambda d: downloads.append(d))  # whole context: download may open a new tab
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(web.URL)
            try:
                page.locator(CAND["avatar"][0]).first.wait_for(state="attached", timeout=20000)
            except Exception:
                pass
            if "google.com/sorry" in page.url:
                grab(page, "00-blocked")
                web.die(1, "Google blocked the automated browser (unusual traffic page)")
            if "accounts.google.com" in page.url or not page.locator(CAND["avatar"][0]).count():
                grab(page, "00-notsignedin")
                web.die(3, "not logged in to Gemini. Ask the user to run: gemini-web login")
            grab(page, "01-loaded")
            for s in CAND["temp_chat"]:  # temporary chat
                if visible(page, s):
                    page.locator(s).first.click()
                    break
            page.wait_for_timeout(1000)
            grab(page, "02-temp-chat")
            for s in CAND["mode_picker"]:  # open mode picker, snapshot menu, close
                if visible(page, s):
                    page.locator(s).first.click()
                    page.wait_for_timeout(1000)
                    grab(page, "03-mode-menu")
                    page.keyboard.press("Escape")
                    break
            tb = page.locator(CAND["textbox"][0]).first
            tb.fill("Generate an image of a red cube")
            tb.press("Enter")
            page.wait_for_timeout(1000)
            grab(page, "04-streaming")
            t0 = time.monotonic()
            while time.monotonic() - t0 < 240:
                if not visible(page, CAND["stop"][0]) and page.locator(CAND["response"][0]).count():
                    break
                page.wait_for_timeout(500)
            grab(page, "05-stop-gone")  # right after Stop disappears
            page.wait_for_timeout(12000)  # image may land after Stop is gone
            out = grab(page, "06-final")
            if out["download_full"]:
                page.locator(CAND["image"][0]).first.hover()
                try:
                    with ctx.expect_event("download", timeout=60000) as ev:
                        page.locator(CAND["download_full"][0]).first.click()
                    d = ev.value
                    d.save_as(str(web.HOME / "probe" / ("dl-" + d.suggested_filename)))
                    STATE["download"] = d.suggested_filename
                except Exception as e:
                    STATE["download_error"] = str(e)[:300]
            STATE["final_url"] = page.url
            json.dump(STATE, open(web.HOME / "probe" / "probe.json", "w"), indent=1)
            nulls = [k for k, v in out.items() if v is None]
            print(f"probe done: {web.HOME / 'probe'}; null keys: {nulls}")
            ctx.close()
        except SystemExit as e:  # SystemExit inside the context hangs the driver
            web.hard_exit(e.code)


if __name__ == "__main__":
    main()
