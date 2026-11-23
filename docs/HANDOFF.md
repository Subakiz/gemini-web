# gemini-web handoff

Status: final review PASS on 2026-11-23.
- Offline tests: 28/28 pass. Run with `uv run --with pytest pytest -q` from the project root. Takes about 6.5 minutes.
- Live checks: `ask` works. `image` works and saves a 2752x1536 full-size file.
- Live checks not yet run: LIVE-2 (conversation history), LIVE-4 (`--model` switching), LIVE-6 (session still valid after 24 h).

## What was built
- `gemini_web/cli.py`: argparse commands (`ask`, `image`, `login`), exit codes, run lock, stdout/stderr split.
- `gemini_web/web.py`: `SELECTORS` dict (all page selectors live here), browser launch, chat, image download, login command.
- `scripts/probe.py`: dumps the live Gemini DOM for selector repair.
- `pyproject.toml`: pins `playwright==1.61.*`; script entry `gemini-web`.
- `tests/fake_gemini.html` and `tests/test_offline.py`: offline fake of the Gemini page, tested with real headless Chrome.
- `docs/PLAN.md`: design, CLI contract, acceptance criteria.
- `~/.claude/skills/gemini-web/SKILL.md`: usage notes for Claude.
- State folder: `~/Library/Application Support/gemini-web/` (mode 700). Holds `profile/`, `run.lock`, `probe/`, and `last-failure.*`.

## Re-login
Session expired or "not logged in" (exit 3):
1. Run `gemini-web login`.
2. Sign in in the Chrome window that opens.
3. Quit that window with Cmd-Q. The command then checks the login and prints `logged in`.

## Fix UI breakage
If a command exits 1 with "UI element '<key>' not found", Google changed the page.
1. Run the probe to dump the live DOM:
   `uv run --with "playwright==1.61.*" scripts/probe.py`
   Output goes to `~/Library/Application Support/gemini-web/probe/`.
2. Find the new selector in that dump.
3. Update the matching entry in the `SELECTORS` dict in `gemini_web/web.py`.
4. Run the offline tests, then one live `ask` and one live `image`.
A failed run also saves `last-failure.png` and `last-failure.html` in the state folder. Look there first. They may contain the prompt and reply, so do not share them.

## Iteration log
- Plan loop, round 1: critic FAIL (3 blocking: late live discovery, positive login check, rate-limit phrase check). Round 2: PASS.
- CP0 probe: avatar check bug (hidden element). Fixed. PASS (headless works, full-size download works).
- CP1 round 1: tester PASS 16/16; reviewer FAIL (3: SystemExit hang without Chrome, fake sorry mode, AC-6 pro). Round 2: PASS for both.
- CP2 round 1: tester PASS 22/22; reviewer FAIL (2: late image dropped, `--timeout` ignored after reply). Round 2: reviewer PASS, tester 24/24.
- CP3: reviewer PASS (8 doc notes applied). Tester FAIL (AC-10 focus flake caused by user activity). Fix: test now attributes the app. 28/28 PASS.
- Final review (opus): PASS.

## Open items (not blocking)
- `playwright-artifacts-*` temp dirs are left behind by failed runs (28 seen in `$TMPDIR`).
- Temp-chat click is not confirmed (`web.py` lines 136-137).
- Unplanned exceptions skip the fast exit, so the browser takes about 25 s to close.
- `_free_path` has a race window (TOCTOU).
- Unverified live checks: LIVE-2, LIVE-4, LIVE-6 (see Status).
- `SKILL.md` examples use `/tmp/gw`. Change to a durable path.

## Security
The login profile uses Chromium's mock keychain. Its cookies are encrypted with a fixed, public key. Any process running as your macOS user can read them and take over the whole Google session. Time Machine copies `profile/` and `last-failure.*`.
Consider a secondary Google account for this tool. To revoke: delete the state folder and sign out of the session on your Google account's device page.
