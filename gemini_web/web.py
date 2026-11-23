"""Chrome launch, login, session and ask. Selectors come from the CP0 live dump (probe.json)."""
import fcntl, os, plistlib, subprocess, sys, time
from pathlib import Path

CHROME_APP = "/Applications/Google Chrome.app"
CHROME_BIN = CHROME_APP + "/Contents/MacOS/Google Chrome"
URL = os.environ.get("GEMINI_WEB_URL", "https://gemini.google.com/app?hl=en")
HOME = Path(os.environ.get("GEMINI_WEB_HOME", Path.home() / "Library/Application Support/gemini-web"))
PROFILE = HOME / "profile"
LOCK = HOME / "run.lock"
KEYCHAIN_ARGS = ["--use-mock-keychain", "--password-store=basic"]
# All DOM knowledge lives here. Taken from the CP0 live dump (2026-11-23); fix a UI change in this dict.
SELECTORS = {
    "avatar": '[aria-label*="Google Account"]',  # 2 matches live, first is hidden: test attached, not visible
    "textbox": 'div[role="textbox"][aria-label="Enter a prompt for Gemini"]',
    "temp_on": 'button[aria-label="Turn on temporary chat"]',
    "temp_off": 'button[aria-label="Turn off temporary chat"]',  # label after the click: already on is fine
    "mode_picker": "bard-mode-switcher button",
    "mode_item": "gem-menu-item",
    "stop": 'button[aria-label="Stop response"]',
    "response": "model-response",
    "content": "model-response message-content",  # answer only: no "Show thinking", no footer
    "image": 'img[alt*="AI generated"]',  # inside the last model-response; live src is a blob: preview
    "download": '[aria-label*="Download full size"]',  # in the image's hover toolbar
}


def die(code, msg):
    print(f"gemini-web: {msg}", file=sys.stderr)
    sys.exit(code)


def ensure_home():
    for d in (HOME, PROFILE):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)


def chrome_major():
    try:
        with open(CHROME_APP + "/Contents/Info.plist", "rb") as f:
            return plistlib.load(f)["CFBundleShortVersionString"].split(".")[0]
    except OSError:
        die(1, f"Google Chrome not found at {CHROME_APP}")


def login_argv(profile=PROFILE):
    """Plain Chrome, no automation flags. Used by login and nothing else."""
    return [CHROME_BIN, f"--user-data-dir={profile}", *KEYCHAIN_ARGS,
            "--no-first-run", "--no-default-browser-check", URL]


def launch_kwargs():
    """Kwargs for playwright launch_persistent_context; shared by probe and (later) ask/image."""
    ua = (f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          f"(KHTML, like Gecko) Chrome/{chrome_major()}.0.0.0 Safari/537.36")
    return dict(channel="chrome", headless=True, locale="en-US", accept_downloads=True,
                viewport={"width": 1280, "height": 900}, user_agent=ua,
                args=[*KEYCHAIN_ARGS, "--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"])


def acquire_lock(wait=300):
    """flock run.lock; returns the open file (keep it referenced). Exit 1 after `wait` seconds."""
    ensure_home()
    f = open(LOCK, "a+")
    end = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except BlockingIOError:
            if time.monotonic() >= end:
                die(1, f"another gemini-web run held the lock for {wait} s; try again later")
            time.sleep(0.5)


def save_failure(page):
    """Screenshot + html of the failing page; may hold the prompt, reply and page tokens. Returns png path."""
    clear_failure()
    png = HOME / "last-failure.png"
    try:
        page.screenshot(path=str(png))
        (HOME / "last-failure.html").write_text(page.content())
    except Exception:
        pass
    return png


def clear_failure():
    for n in ("last-failure.png", "last-failure.html"):
        (HOME / n).unlink(missing_ok=True)


def fail_ui(page, key):
    die(1, f"UI element '{key}' not found (Gemini UI may have changed); screenshot: {save_failure(page)}")


def need(page, key, timeout=20000):
    loc = page.locator(SELECTORS[key]).first
    try:
        loc.wait_for(state="visible", timeout=timeout)
    except Exception:
        fail_ui(page, key)
    return loc


def check_session(page):
    """Positive login check: avatar attached within 20 s. Exit 1 on the sorry page, 3 otherwise."""
    end = time.monotonic() + 20
    while time.monotonic() < end:
        if "google.com/sorry" in page.url:
            save_failure(page)
            die(1, "Google blocked the automated browser (unusual traffic page)")
        if "accounts.google.com" in page.url:
            break
        try:
            if page.locator(SELECTORS["avatar"]).count():
                return
        except Exception:
            pass  # page navigating; poll again
        page.wait_for_timeout(250)
    die(3, "not logged in to Gemini. Ask the user to run: gemini-web login")


def open_chat(page):
    page.goto(URL)
    check_session(page)
    need(page, "textbox")
    both = page.locator(f'{SELECTORS["temp_on"]}, {SELECTORS["temp_off"]}').first
    try:
        both.wait_for(state="visible", timeout=5000)
    except Exception:
        print("gemini-web: warning: temporary chat button not found; this chat will be saved in your Gemini history", file=sys.stderr)
        return
    if page.locator(SELECTORS["temp_on"]).count():
        page.locator(SELECTORS["temp_on"]).first.click()


def pick_model(page, name):
    """Click the first mode-menu item whose title contains name. Exit 2 with the titles if none."""
    need(page, "mode_picker").click()
    items = page.locator(SELECTORS["mode_item"])
    try:
        items.first.wait_for(state="visible", timeout=10000)
    except Exception:
        fail_ui(page, "mode_item")
    found = []  # (title, locator); title = first line of the item; submenu parents and Upgrade items are skipped
    for i in range(items.count()):
        it = items.nth(i)
        text = it.inner_text()
        # the Thinking-level submenu items sit in the DOM hidden: only visible items are models
        if not it.is_visible() or it.get_attribute("aria-haspopup") == "true" or "upgrade" in text.lower():
            continue
        found.append((next((l.strip() for l in text.splitlines() if l.strip()), ""), it))
    for title, it in found:
        if name.lower() in title.lower():
            it.click()
            return
    page.keyboard.press("Escape")
    die(2, f"no model matching '{name}'; available: " + ", ".join(t for t, _ in found))


def _send(page, prompt, timeout, model):
    open_chat(page)
    if model:
        pick_model(page, model)
    resp, n0 = page.locator(SELECTORS["response"]), page.locator(SELECTORS["response"]).count()
    box = need(page, "textbox")
    box.fill(prompt)
    box.press("Enter")
    t0 = time.monotonic()
    while resp.count() <= n0:
        if time.monotonic() - t0 > min(30, timeout):
            fail_ui(page, "send")
        page.wait_for_timeout(250)
    return t0


def _loaded(page):
    """Number of fully loaded generated images in the last response."""
    imgs = page.locator(SELECTORS["response"]).last.locator(SELECTORS["image"])
    return imgs.evaluate_all("els => els.filter(e => e.complete && e.naturalWidth > 0).length")


def _wait_reply(page, t0, timeout, image=False):
    """Text of the last reply once Stop is gone and the text has not changed for 2 s.
    For images an empty text is fine if an image has loaded."""
    last, since = None, time.monotonic()
    while True:
        now = time.monotonic()
        if now - t0 > timeout:
            die(4, f"no complete reply after {timeout} s; screenshot: {save_failure(page)}")
        text = page.locator(SELECTORS["content"]).last.inner_text() if page.locator(SELECTORS["content"]).count() else ""
        if text != last:
            last, since = text, now
        if not page.locator(SELECTORS["stop"]).first.is_visible() and now - since >= 2 \
                and (last.strip() or (image and _loaded(page))):
            return last.strip()
        page.wait_for_timeout(500)


def ask(page, prompt, timeout=180, model=None):
    t0 = _send(page, prompt, timeout, model)
    text = _wait_reply(page, t0, timeout)
    clear_failure()
    return text


def _free_path(out_dir, name):
    """out_dir/name, or name-1.ext, name-2.ext ... : never overwrites."""
    name = Path(name).name or "gemini-image"
    p, n = out_dir / name, 0
    while p.exists():
        n += 1
        p = out_dir / f"{Path(name).stem}-{n}{Path(name).suffix}"
    return p


def image(page, prompt, out_dir, timeout=240):
    """Generate images and save the full-size files. Returns (paths, reply text).
    One deadline (t0 + timeout) covers every phase; running past it exits 4."""
    t0 = _send(page, "Generate an image: " + prompt, timeout, None)
    deadline = t0 + timeout

    def left():
        return deadline - time.monotonic()

    def timed_out():
        die(4, f"no complete download after {timeout} s; screenshot: {save_failure(page)}")

    text = _wait_reply(page, t0, timeout, image=True)
    end = min(time.monotonic() + 15, deadline)  # the image can land after Stop is gone
    while not _loaded(page) and time.monotonic() < end:
        page.wait_for_timeout(500)
    if not _loaded(page):
        if left() <= 0:
            timed_out()
        die(1, f"Gemini returned no image: {text}")
    last = page.locator(SELECTORS["response"]).last
    imgs, btns = last.locator(SELECTORS["image"]), last.locator(SELECTORS["download"])
    # a later image can arrive seconds after the first: wait until all three counts hold still for 5 s
    state, since = None, time.monotonic()
    while True:
        now = time.monotonic()
        cur = (_loaded(page), imgs.count(), btns.count())
        if cur != state:
            state, since = cur, now
        if now - since >= 5:
            break
        if left() <= 0:
            timed_out()
        page.wait_for_timeout(500)
    n_loaded, n_imgs, n_btns = state
    if n_loaded != n_imgs or n_imgs != n_btns:
        die(1, f"found {n_imgs} images ({n_loaded} loaded) and {n_btns} download buttons; "
               f"screenshot: {save_failure(page)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    try:
        for i in range(n_btns):
            try:
                imgs.nth(i).hover(timeout=max(1, min(left(), 30)) * 1000)  # the download button only shows on hover
                with page.context.expect_event("download", timeout=max(1, min(left(), 60)) * 1000) as ev:  # whole context: may open a new tab
                    btns.nth(i).click(timeout=max(1, min(left(), 30)) * 1000)
            except Exception:
                if left() <= 0:
                    timed_out()
                fail_ui(page, "download")
            dl = ev.value
            path = _free_path(out_dir, dl.suggested_filename)
            dl.save_as(str(path))
            saved.append(path)
    except SystemExit:
        for p in saved:  # no partial results: stdout is empty on failure, so leave no files either
            p.unlink(missing_ok=True)
        raise
    clear_failure()
    return [str(p.resolve()) for p in saved], text


def hard_exit(code):
    """Exit from inside a Playwright context. A normal SystemExit there hangs the driver and ctx.close()
    can take 25 s, so kill this profile's Chrome, wait up to 5 s for it to go, and leave without cleanup."""
    pat = f"--user-data-dir={PROFILE}"
    subprocess.run(["pkill", "-TERM", "-f", "--", pat])
    for _ in range(20):
        if subprocess.run(["pgrep", "-f", "--", pat], capture_output=True).returncode:
            break
        time.sleep(0.25)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code if isinstance(code, int) else 0)


def _run(fn):
    from playwright.sync_api import sync_playwright
    kw = launch_kwargs()  # may die(1) "Chrome not found": must happen outside the Playwright context
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE), **kw)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(30000)
            result = fn(page)
        except SystemExit as e:
            hard_exit(e.code)
        ctx.close()  # success path keeps the normal close
    return result


def session(fn):
    lock = acquire_lock(300)  # noqa: F841 (held until return)
    return _run(fn)


def login():
    lock = acquire_lock(wait=0)  # refuse if busy; held until Chrome exits
    if not os.path.exists(CHROME_BIN):
        die(1, f"Google Chrome not found at {CHROME_APP}")
    print("gemini-web: Sign in to Gemini in the window that opened, then quit that Chrome window (Cmd-Q).", file=sys.stderr)
    subprocess.Popen(login_argv()).wait()
    _run(lambda page: (page.goto(URL), check_session(page)))  # exits 3 if not signed in
    print("logged in")
    lock.close()
