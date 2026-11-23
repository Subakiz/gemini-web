"""CP1 offline tests for gemini-web. Real headless Chrome against tests/fake_gemini.html. No Google.

Each test name carries its acceptance criterion (AC) from docs/PLAN.md section 7.
Set GEMINI_WEB_TEST_BASE to a throwaway dir; it must not be the real profile.

Not testable offline here (reason):
- AC-0a..0d: live CP0 probe against the real Gemini page.
- AC-14: removed (no URL-rewrite fallback). AC-12, 13, 15, 16 are tested below in the CP2 section.
- AC-17, AC-18: CP3 install and skill file.
- LIVE-1..7: need the user's Google login.
- AC-9 at CLI level: session() waits 300 s for the lock. Tested at helper level (acquire_lock with wait=1).
- login() interactive flow: only its argv builder is tested (AC-11).
"""
import fcntl
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FAKE = REPO / "tests" / "fake_gemini.html"
BASE = Path(os.environ.get("GEMINI_WEB_TEST_BASE") or tempfile.mkdtemp(prefix="gw-test-"))
HOME = BASE / "home"
REAL = Path.home() / "Library" / "Application Support" / "gemini-web"
assert REAL not in HOME.resolve().parents and HOME.resolve() != REAL, "refusing to use the real profile"
shutil.rmtree(HOME, ignore_errors=True)
HOME.mkdir(parents=True)
os.environ["GEMINI_WEB_HOME"] = str(HOME)
sys.path.insert(0, str(REPO))

REPLY = 'Reply to "hello" (len=5, temp=on, model=Everyday help). Done.'


def page_uri(mode="", page=FAKE):
    return page.as_uri() + (f"?mode={mode}" if mode else "")


def run(args, *, stdin=None, mode="", page=FAKE, timeout=150):
    env = dict(os.environ, GEMINI_WEB_HOME=str(HOME), GEMINI_WEB_URL=page_uri(mode, page))
    return subprocess.run([sys.executable, "-m", "gemini_web.cli", *args], input=stdin,
                          capture_output=True, text=True, env=env, cwd=str(REPO), timeout=timeout)


def test_ac1_ask_full_reply_stdout_only():
    r = run(["ask", "hello"])
    assert r.returncode == 0, r.stderr
    assert r.stdout == REPLY + "\n"  # full final reply, nothing else on stdout


def test_ac2_20000_char_prompt_via_stdin():
    prompt = "0123456789" * 2000  # 20,000 chars
    r = run(["ask", "-"], stdin=prompt)
    assert r.returncode == 0, r.stderr
    # fake echoes only the first 20 chars and the length: length check, not full content
    assert 'Reply to "01234567890123456789"' in r.stdout and "len=20000" in r.stdout, r.stdout


def test_ac3_signedout_exit3():
    r = run(["ask", "hello"], mode="signedout")
    assert r.returncode == 3, r.stderr
    assert "gemini-web login" in r.stderr and r.stdout == ""


def test_ac3_anon_exit3():
    r = run(["ask", "hello"], mode="anon")  # textbox present, no avatar
    assert r.returncode == 3, r.stderr
    assert "gemini-web login" in r.stderr and r.stdout == ""


def test_ac3_sorry_exit1_blocked():
    # fake sets its own URL to google.com/sorry in this mode (history.replaceState)
    r = run(["ask", "hello"], mode="sorry")
    assert r.returncode == 1, r.stderr
    assert "blocked" in r.stderr


def test_ac4_hang_timeout_exit4_with_screenshot():
    t0 = time.monotonic()
    r = run(["ask", "hello", "--timeout", "5"], mode="hang")
    elapsed = time.monotonic() - t0
    assert r.returncode == 4, r.stderr
    assert elapsed < 20, elapsed
    assert "no complete reply after 5 s; screenshot:" in r.stderr
    shot = Path(r.stderr.split("screenshot:", 1)[1].strip())
    assert shot.exists(), shot


def test_ac5_notextbox_names_key_then_success_clears_failure():
    r = run(["ask", "hello"], mode="notextbox")
    assert r.returncode == 1, r.stderr
    assert "UI element 'textbox' not found" in r.stderr
    assert (HOME / "last-failure.png").exists() and (HOME / "last-failure.html").exists()
    ok = run(["ask", "hello"])
    assert ok.returncode == 0, ok.stderr
    assert not (HOME / "last-failure.png").exists() and not (HOME / "last-failure.html").exists()


def test_ac6_model_auto_clicks_matching_item():
    r = run(["ask", "hello", "--model", "auto"])
    assert r.returncode == 0, r.stderr
    assert "model=Auto)" in r.stdout, r.stdout


def test_ac6_unknown_model_exit2_lists_titles():
    r = run(["ask", "hello", "--model", "zzz"])
    assert r.returncode == 2, r.stderr
    # "Deep think" is skipped (Upgrade item); "Thinking level" submenu and hidden items skipped
    assert "no model matching 'zzz'; available: Auto, Everyday help" in r.stderr, r.stderr


def test_ac7_temp_chat_clicked_by_default():
    r = run(["ask", "hello"])
    assert r.returncode == 0, r.stderr
    assert "temp=on" in r.stdout and "warning" not in r.stderr


def test_ac7_notemp_warns_and_exits0():
    r = run(["ask", "hello"], mode="notemp")
    assert r.returncode == 0, r.stderr
    assert "warning: temporary chat button not found; this chat will be saved in your Gemini history" in r.stderr
    assert "temp=off" in r.stdout


def test_cli_contract_stderr_lines_prefixed():
    # PLAN sec 3: every stderr line starts with "gemini-web:"
    r = run(["ask", "hello"], mode="notemp")
    lines = [l for l in r.stderr.splitlines() if l.strip()]
    assert lines and all(l.startswith("gemini-web:") for l in lines), r.stderr


def test_ac9_lock_helper_busy_exit1():
    import fcntl as _f
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / "run.lock", "a+") as held:
        _f.flock(held, _f.LOCK_EX | _f.LOCK_NB)
        env = dict(os.environ, GEMINI_WEB_HOME=str(HOME))
        r = subprocess.run([sys.executable, "-c", "from gemini_web import web; web.acquire_lock(1)"],
                           capture_output=True, text=True, env=env, cwd=str(REPO), timeout=60)
    assert r.returncode == 1, r.stderr
    assert "another gemini-web run held the lock for 1 s; try again later" in r.stderr


def test_ac9_two_concurrent_runs_both_exit0():
    p1 = subprocess.Popen([sys.executable, "-m", "gemini_web.cli", "ask", "first"], cwd=str(REPO),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          env=dict(os.environ, GEMINI_WEB_HOME=str(HOME), GEMINI_WEB_URL=page_uri()))
    p2 = subprocess.Popen([sys.executable, "-m", "gemini_web.cli", "ask", "second"], cwd=str(REPO),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          env=dict(os.environ, GEMINI_WEB_HOME=str(HOME), GEMINI_WEB_URL=page_uri()))
    o1, e1 = p1.communicate(timeout=200)
    o2, e2 = p2.communicate(timeout=200)
    assert p1.returncode == 0, e1
    assert p2.returncode == 0, e2
    assert 'Reply to "first"' in o1 and 'Reply to "second"' in o2


def _front():
    return subprocess.run(["lsappinfo", "front"], capture_output=True, text=True).stdout.strip()


def _ancestors(pid):
    out = []
    while pid > 1 and len(out) < 50:
        out.append(pid)
        ppid = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        if not ppid.isdigit():
            break
        pid = int(ppid)
    return out


def _assert_no_own_focus(before, seen):
    """Fail only if the front app became Chrome/Chromium or a descendant of this test process.
    Any other app taking focus (the user at the Mac) is logged as info, not a failure."""
    own = []
    for asn in sorted(set(seen) - {before}):
        info = subprocess.run(["lsappinfo", "info", "-only", "name", "-only", "bundleid", "-only", "pid", asn],
                              capture_output=True, text=True).stdout
        m_name = re.search(r'"([^"]*)"', info)
        m_bundle = re.search(r'bundleID="([^"]*)"', info)
        m_pid = re.search(r"pid = (\d+)", info)
        name = m_name.group(1) if m_name else "?"
        bundle = m_bundle.group(1) if m_bundle else ""
        pid = int(m_pid.group(1)) if m_pid else None
        chromeish = any(k in (name + bundle).lower() for k in ("chrome", "chromium"))
        ours = chromeish or (pid is not None and os.getpid() in _ancestors(pid))
        print(f"info: front {asn} = {name} ({bundle}) pid={pid} ours={ours}")
        if ours:
            own.append((asn, name, bundle, pid))
    assert not own, own


def test_ac10_no_focus_change_during_ask():
    before = _front()
    samples, stop = [], threading.Event()

    def sampler():
        while not stop.is_set():
            samples.append(_front())
            time.sleep(0.25)

    t = threading.Thread(target=sampler)
    t.start()
    try:
        r = run(["ask", "hello"])
    finally:
        stop.set()
        t.join()
    after = _front()
    assert r.returncode == 0, r.stderr
    assert before and samples, (before, samples)
    _assert_no_own_focus(before, samples + [after])


def test_ac11_login_argv_and_headless_launch_args():
    import gemini_web.web as web
    argv = web.login_argv()
    assert argv[0] == web.CHROME_BIN
    assert f"--user-data-dir={web.PROFILE}" in argv
    assert "--use-mock-keychain" in argv
    assert not any(a == "--enable-automation" or a.startswith("--remote-debugging") for a in argv)
    kw = web.launch_kwargs()
    assert "--use-mock-keychain" in kw["args"] and "--password-store=basic" in kw["args"]
    assert "--enable-automation" in kw["ignore_default_args"]
    assert kw["channel"] == "chrome" and kw["headless"] is True


# ---- CP2: image ----
import hashlib
import json

FIXTURE = REPO / "tests" / "fixtures" / "full_2048.png"
FIXTURE_SHA = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
SUGGESTED = "Gemini_Generated_Image_fake.png"  # the name the fake page's download sends


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def sips_dims(p):
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(p)],
                         capture_output=True, text=True, check=True).stdout
    vals = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            vals[k.strip()] = v.strip()
    return int(vals["pixelWidth"]), int(vals["pixelHeight"])


def new_dir(name):
    d = BASE / "img" / name  # wiped so reruns start clean; the tool creates "out" itself
    shutil.rmtree(d, ignore_errors=True)
    return d / "out"


def test_ac12_image_saves_fullsize_fixture_bytes_and_prints_path():
    out = new_dir("ac12")
    assert not out.exists()
    r = run(["image", "a cube", "-o", str(out)], mode="", timeout=300)
    assert r.returncode == 0, r.stderr
    assert out.is_dir(), "directory not created"
    saved = out / SUGGESTED
    assert saved.exists(), list(out.iterdir())
    assert r.stdout == str(saved.resolve()) + "\n", r.stdout
    assert sha(saved) == FIXTURE_SHA, "not the 2048 fixture bytes (preview?)"
    assert sips_dims(saved) == (2048, 2048)


def test_ac12_late_image_still_saved():
    # image lands 5 s after Stop disappears; the 15 s wait must catch it
    out = new_dir("ac12late")
    r = run(["image", "a cube", "-o", str(out)], mode="lateimage", timeout=300)
    assert r.returncode == 0, r.stderr
    assert sha(out / SUGGESTED) == FIXTURE_SHA


def test_ac13_json_one_line_shape():
    out = new_dir("ac13")
    r = run(["image", "a cube", "-o", str(out), "--json"], timeout=300)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert len(lines) == 1, r.stdout
    data = json.loads(lines[0])
    assert set(data) == {"paths", "text"}, data
    assert isinstance(data["paths"], list) and len(data["paths"]) == 1, data
    assert data["paths"][0] == str((out / SUGGESTED).resolve()), data
    assert isinstance(data["text"], str) and data["text"] == "Here is an image: a cube", data
    assert sha(data["paths"][0]) == FIXTURE_SHA


def test_ac15_noimage_exit1_reply_on_stderr_no_files_no_dir():
    out = new_dir("ac15")
    r = run(["image", "a cube", "-o", str(out)], mode="noimage", timeout=300)
    assert r.returncode == 1, r.stderr
    assert "Gemini returned no image: Sorry, I can't create that image." in r.stderr, r.stderr
    assert r.stdout == ""
    assert not out.exists(), "directory created on noimage"


def test_ac16_two_runs_same_dir_no_overwrite():
    out = new_dir("ac16")
    r1 = run(["image", "a cube", "-o", str(out)], timeout=300)
    r2 = run(["image", "a cube", "-o", str(out)], timeout=300)
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    p1, p2 = r1.stdout.strip(), r2.stdout.strip()
    assert p1 != p2, (p1, p2)
    assert Path(p1).name == SUGGESTED and Path(p2).name == "Gemini_Generated_Image_fake-1.png", (p1, p2)
    assert sha(p1) == FIXTURE_SHA and sha(p2) == FIXTURE_SHA
    assert len(list(out.iterdir())) == 2


def test_ac10_image_no_focus_change():
    out = new_dir("ac10img")
    before = _front()
    samples, stop = [], threading.Event()

    def sampler():
        while not stop.is_set():
            samples.append(_front())
            time.sleep(0.25)

    t = threading.Thread(target=sampler)
    t.start()
    try:
        r = run(["image", "a cube", "-o", str(out)], timeout=300)
    finally:
        stop.set()
        t.join()
    after = _front()
    assert r.returncode == 0, r.stderr
    assert before and samples, (before, samples)
    _assert_no_own_focus(before, samples + [after])


def test_ac12_two_images_second_4s_late_both_saved():
    out = new_dir("ac12two")
    r = run(["image", "a cube", "-o", str(out)], mode="two", timeout=300)
    assert r.returncode == 0, r.stderr
    paths = r.stdout.splitlines()
    assert len(paths) == 2, r.stdout
    assert [Path(p).name for p in paths] == [SUGGESTED, "Gemini_Generated_Image_fake-1.png"], paths
    assert all(sha(p) == FIXTURE_SHA for p in paths)
    assert len(list(out.iterdir())) == 2


def test_ac12_timeout_no_complete_download_exit4_no_files():
    out = new_dir("ac12nodl")
    t0 = time.monotonic()
    r = run(["image", "a cube", "-o", str(out), "--timeout", "20"], mode="nodl", timeout=150)
    elapsed = time.monotonic() - t0
    assert r.returncode == 4, r.stderr
    assert "no complete download after 20 s; screenshot:" in r.stderr, r.stderr
    assert r.stdout == ""
    assert elapsed < 60, elapsed
    assert not out.exists() or list(out.iterdir()) == [], list(out.iterdir())


# ---- CP3: installed binary and skill (AC-17, AC-18) ----
INSTALLED = Path.home() / ".local" / "bin" / "gemini-web"
SKILL_MD = Path.home() / ".claude" / "skills" / "gemini-web" / "SKILL.md"


def run_installed(args, *, stdin=None, mode="", timeout=150):
    env = dict(os.environ, GEMINI_WEB_HOME=str(HOME), GEMINI_WEB_URL=page_uri(mode))
    return subprocess.run([str(INSTALLED), *args], input=stdin, capture_output=True, text=True,
                          env=env, cwd=str(REPO), timeout=timeout)


def test_ac17_installed_binary_help_exit0():
    assert INSTALLED.exists(), INSTALLED  # uv tool install --editable puts it here
    r = subprocess.run([str(INSTALLED), "--help"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert all(w in r.stdout for w in ("ask", "image", "login")), r.stdout


def test_ac17_installed_binary_ask_dash_and_stdin():
    r = run_installed(["ask", "--", "-v vs --verbose?"])
    assert r.returncode == 0, r.stderr
    assert 'Reply to "-v vs --verbose?"' in r.stdout, r.stdout
    r2 = run_installed(["ask", "-"], stdin="hello stdin")
    assert r2.returncode == 0, r2.stderr
    assert 'Reply to "hello stdin" (len=11' in r2.stdout, r2.stdout


def test_ac17_installed_binary_image_json():
    out = new_dir("ac17img")
    r = run_installed(["image", "a cube", "-o", str(out), "--json"], timeout=300)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert len(data["paths"]) == 1 and sha(data["paths"][0]) == FIXTURE_SHA


# AC-18 keyword checks: each item the plan requires must appear in SKILL.md
SKILL_REQUIRED = {
    "frontmatter name": "name: gemini-web",
    "frontmatter description": "description: ",
    "second opinion": "second opinion",
    "image generation": "image",
    "ask example": "gemini-web ask ",
    "image example": "gemini-web image ",
    "exit 3 ask user to run login": "| 3 |",
    "never run login yourself": "Never run login yourself",
    "exit 1/4 retry at most once": "retry at most once",
    "bash timeout 600000": "600000",
    "prompt starting with - after --": 'ask -- "',
    "stdin ask -": "ask - <<",
    "last-failure.html content warning": "last-failure.html",
    "10-90 s limit": "10-90 s",
    "one run at a time": "One run at a time",
    "no attachments": "No attachments",
    "plain-text answers": "plain text",
    "privacy: no secrets": "Never send secrets",
    "consumer service note": "consumer Google service",
}


def test_ac18_skill_frontmatter_and_required_items():
    text = SKILL_MD.read_text()
    assert text.startswith("---\nname: gemini-web\ndescription: ")
    missing = [k for k, v in SKILL_REQUIRED.items() if v not in text]
    assert not missing, missing
