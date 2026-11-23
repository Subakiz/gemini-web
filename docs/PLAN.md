# gemini-web: build plan

A small personal CLI that lets Claude Code use the consumer Gemini web app (gemini.google.com, the user's own Google account) from a shell: ask for a text second opinion, or generate images and save them at full resolution. It runs headless and must never take focus from the user.

## 1. Approach

**Primary path: Playwright (Python) drives the real Google Chrome (channel="chrome") headless, with a dedicated persistent profile, through the normal Gemini web UI.** The browser keeps its own Google session fresh, and image downloads use Gemini's own "Download full size image" button.

Why:
- Chrome refreshes and rotates its own session cookies, including any device-bound ones. Nothing has to copy cookies out of the browser.
- One fragile layer (DOM selectors) instead of two (a private RPC format plus a cookie export). All selectors sit in one dict, and a failure saves a screenshot, so a UI change is a one-line fix.
- One dependency: `playwright==1.61.*`, which uv installs from PyPI into the tool's own environment. It is pinned because the cookie setup depends on Playwright's launch flags (see below). There is no browser download, because the tool uses the installed Chrome 158.

Evidence from spikes (scratchpad `arch/`):
- Playwright 1.61.0 always adds `--use-mock-keychain` and `--password-store=basic` when it launches Chrome (`driver/package/lib/coreBundle.js`). So the profile's cookies are encrypted with the mock key. **The login browser must be started with the same `--use-mock-keychain` flag**, or later headless runs cannot decrypt the cookies and look logged out.
- With `headless=True`, Playwright passes `--headless` to the real Chrome binary. In current Chrome that means the "new headless" mode, which is the full browser and not the headless shell. Its user agent string contains "HeadlessChrome", so the tool overrides it.
- gemini-webapi 2.1.1 (read from the unpacked wheel): full-size images come from a private RPC followed by `=d-I?alr=yes`, with a fallback that rewrites the URL suffix to `=s2048-rj`. Temporary chat is a flag in the RPC payload. Both features exist on the server side, and the UI exposes both.
- hermes-gem-pw: runs a *headed* Chromium because "headed mode is what slips past Google's anti-automation". It uses the bundled Chromium on Linux, so this is not proof that headless Chrome fails on macOS. It is still the top risk and gets a LIVE check (LIVE-1) plus the one fallback below. The project also uses fixed sleeps and zh-TW selectors. Take ideas from it only, and copy no code.

**One fallback, chosen in CP0 only if the probe shows Google blocking headless:** start the real Chrome *headed* with `open -g -n -a "Google Chrome" --args --user-data-dir=PROFILE --use-mock-keychain --remote-debugging-port=PORT --window-position=-3000,-3000` (`-g` = do not bring it to the front), then `connect_over_cdp`. Only the launch function changes. All page logic is shared.

Rejected (one line each):
- gemini-webapi as the backend: open bugs as of 2026-11-22 (init 400 #383, init deadlock #381, streaming stalls #374). Its own README says exported Chromium cookies are device-bound and expire within hours. AGPL.
- Hybrid (Playwright keeps the cookies fresh, gemini-webapi makes the calls): two fragile layers and the same RPC bugs.
- Native Gemini.app via Accessibility: steals focus, and images are only 512x286 previews.
- Gemini API key: rejected by the user.
- Logging in through a Playwright-launched browser: Google may refuse to sign in an automation-flagged browser.
- Attachments (`--file`): not cheap to do reliably. Claude can paste text into the prompt through stdin instead.

## 2. File layout and ownership

```
./
  scripts/probe.py          builder CP0   about 40 lines; live DOM probe after login (section 7, CP0)
  pyproject.toml            builder CP1   deps: playwright==1.61.*; script gemini-web = gemini_web.cli:main
  gemini_web/__init__.py    builder CP1   empty
  gemini_web/cli.py         builder CP1   argparse, exit codes, run lock, stdout/stderr
  gemini_web/web.py         builder CP1+2 SELECTORS dict, launch(), open_chat(), ask(), image(), login argv
  tests/fake_gemini.html    builder CP1+2 offline fake of the Gemini DOM (modes set by query string)
  tests/test_offline.py     tester        pytest, real headless Chrome against the fake page
  docs/PLAN.md              architect
~/.claude/skills/gemini-web/SKILL.md   builder CP3
```
State: `~/Library/Application Support/gemini-web/` (mode 700) holds `profile/`, `run.lock`, `probe/`, `last-failure.png` and `last-failure.html`. The last-failure files can contain the prompt, the reply and page tokens. They are deleted after the next successful run.
Two environment variables exist for tests only: `GEMINI_WEB_URL` (default `https://gemini.google.com/app?hl=en`) and `GEMINI_WEB_HOME` (the state directory).

## 3. CLI contract

```
gemini-web ask [PROMPT] [--model NAME] [--timeout SEC]   # PROMPT "-" or absent (with stdin piped) = read stdin
gemini-web image PROMPT -o DIR [--json] [--timeout SEC]
gemini-web login
```
- `ask`: prints the answer text to stdout followed by one newline. The text is the response element's innerText: paragraphs, lists and code are kept, and markdown syntax is not rebuilt. Default timeout is 180 s.
- `--model NAME`: opens the mode picker and clicks the first menu item whose *title* contains NAME (case-insensitive). Only the title counts, not the description, and items containing "Upgrade" are skipped. With no `--model`, the account default is used. If no item matches, exit 2 and print the available item titles. Submenus (such as "Thinking level") are skipped. The live menu on 2026-11-23 had only "Auto" and "Everyday help", so for example `--model auto` picks Auto.
- `image`: sends `Generate an image: PROMPT`. Creates DIR if needed. Prints one absolute path per line. With `--json`, prints one line: `{"paths": [...], "text": "<reply text>"}`. Default timeout is 240 s.
- `login`: see section 4.
- stdout carries only results. All progress, warnings and errors go to stderr, starting with `gemini-web:`.

| Exit | Meaning | stderr message (core) |
|---|---|---|
| 0 | ok | (warnings only) |
| 1 | any other failure; the stderr line says which | `UI element '<selector key>' not found (Gemini UI may have changed); screenshot: <path>` / `Gemini returned no image: <reply text>` / `Google blocked the automated browser (unusual traffic page)` / `another gemini-web run held the lock for N s; try again later` / `Google Chrome not found at /Applications/Google Chrome.app` / `internal error: <exc>` |
| 2 | usage, unknown model | argparse text / `no model matching 'X'; available: A, B, C` |
| 3 | not logged in | `not logged in to Gemini. Ask the user to run: gemini-web login` |
| 4 | timeout | `no complete reply after N s; screenshot: <path>` |

`ask` always prints Gemini's reply and exits 0, even if the reply is a rate-limit message. The reply text tells Claude what happened. There is no phrase matching.

## 4. Login and session

- `login` refuses to start if `run.lock` is held. It runs `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=PROFILE --use-mock-keychain --password-store=basic --no-first-run --no-default-browser-check https://gemini.google.com/app?hl=en` with `subprocess.Popen`. These are real Chrome flags with no automation flags, so Google sees a normal browser.
- It prints "Sign in to Gemini in the window that opened, then quit that Chrome window (Cmd-Q)." and waits for the process to exit. The profile is separate, so the user's main Chrome is not touched.
- It then runs the headless login check below. It prints `logged in` and exits 0, or exits 3.
- Every run: take `fcntl.flock` on `run.lock` (wait up to 300 s, which is longer than the longest timeout, else exit 1 "busy"). Then call `launch_persistent_context(PROFILE, channel="chrome", headless=True, locale="en-US", accept_downloads=True, viewport=1280x900, user_agent=<normal macOS Chrome UA built from the major version in Chrome's Info.plist>, args=["--use-mock-keychain", "--password-store=basic", "--disable-blink-features=AutomationControlled"], ignore_default_args=["--enable-automation"])` and go to the URL. The keychain flags are passed explicitly, so cookie decryption does not depend on Playwright's defaults.
- Login check (positive): the session counts as signed in only if the account avatar is present (a control whose aria-label contains "Google Account"; CP0 confirms the exact selector) within 20 s. Gemini serves chat to signed-out users in many regions, so a textbox alone proves nothing. If there is no avatar, or the URL is on `accounts.google.com`, exit 3. If the URL is on `google.com/sorry`, exit 1 with "blocked".
- `?hl=en` plus `locale=en-US` pin the UI language, so aria-label selectors work even if the account is set to Indonesian.
- Temporary chat by default: after load, click the button whose aria-label contains "Temporary chat". If it is missing, print `warning: temporary chat button not found; this chat will be saved in your Gemini history` and continue. There is no `--keep` option until someone asks for one.

## 5. Sending and detecting the end of a reply

- Selectors in this section are placeholders. CP0 replaces them with ones taken from the real DOM.
- Fill the textbox (`div[role=textbox]` / `rich-textarea`) with `locator.fill(prompt)`, which is fast for long prompts, then press Enter.
- Start: the count of `model-response` elements must go up within 30 s, else exit 1 (key `send`).
- Done, polled every 500 ms on the last `model-response`: no visible button with aria-label matching /stop/i, **and** the response innerText is non-empty and unchanged for 2 s. For `image`, also wait until a generated `img` in that response has `naturalWidth > 0`, or the reply is final with no image.
- Past `--timeout`: exit 4. Exits 1 (page failures) and 4 save `last-failure.png` and `last-failure.html` first. A successful run deletes them.
- Text comes from the last response's message-content element, not from `body`, so footer text such as "Gemini can make mistakes" never leaks in.

## 6. Full-resolution image download

1. For each generated image in the last response: hover over it, then click the button whose aria-label contains "Download full size". Catch the file with `page.expect_download(timeout=60s)` and `download.save_as()`. This is the same file a user gets, resolved by Gemini's own code.
2. No URL-rewrite fallback. If CP0 shows there is no download button in headless mode, the architect revisits this step before CP2.
3. Name: keep Gemini's suggested filename (for example `Gemini_Generated_Image_<id>.jpeg`). Never overwrite: if the name exists, add `-1`, `-2` and so on before the extension.
4. No image in a final reply: exit 1 with `Gemini returned no image: <reply text>`, and write no files.

## 7. Checkpoints and acceptance criteria

### CP0: live probe (LIVE; runs before CP1)
After the user runs a minimal `gemini-web login` (just the Chrome argv from section 4, built first), `scripts/probe.py` launches headless with the section 4 args. It opens a temporary chat, opens the mode picker, sends "Generate an image of a red cube", and snapshots the page about 1 s in (while streaming) and again when done. It writes `probe/probe.json` (outerHTML, cut to 2,000 characters, for each key below, or `null`) plus screenshots, all inside the mode-700 state folder and never in the repo. SELECTORS and `tests/fake_gemini.html` are written from this dump, with personal data removed. CP1 starts only after CP0 passes.
- AC-0a The final URL is not on `google.com/sorry` or `accounts.google.com`, and the account avatar is found.
- AC-0b `probe.json` has a non-null entry for: textbox, stop button, model-response, message-content, temporary-chat button, mode picker and its menu items, avatar, generated image, download-full-size button. Any null is listed in the CP0 report.
- AC-0c The headless-or-fallback decision is written into this plan with the probe evidence. If headless is blocked, the probe is re-run with the `open -g` + CDP launch, and AC-0a passes there.
  **Decision (probe, 2026-11-23): headless Playwright works.** final_url was `https://gemini.google.com/app?hl=en`, the avatar was present (2 matches), and a full-size download was captured at 2752x1536. The CDP fallback is NOT needed and will not be built.
- AC-0d The SELECTORS dict and the fake page's DOM match the dump (the reviewer compares them).

Offline tests run real headless Chrome against `tests/fake_gemini.html` (file:// via `GEMINI_WEB_URL`, temporary `GEMINI_WEB_HOME`). No Google. Command: `uv run --with pytest pytest -q`. LIVE checks need the user's login and are run by the user or the orchestrator after `gemini-web login`.

The fake page provides: a contenteditable textbox; Enter appends a `model-response` that streams text over about 1.5 s while a "Stop response" button shows; a mode picker; a "Temporary chat" button that records clicks. It also shows an account avatar, except in modes that remove it. Query modes: `signedout`, `anon` (textbox present, no avatar), `hang`, `notextbox`, `noimage`, `notemp`, `sorry`. Image prompts get a 512 px preview `img` plus a download button that serves a 2048x2048 PNG fixture.

### CP1: session, ask, errors, login argv
- AC-1 `ask "hello"` prints the full final streamed reply (not a partial one) and exits 0. Nothing else goes to stdout.
- AC-2 A 20,000-character prompt through `ask -` on stdin reaches the page intact (the fake page echoes the length).
- AC-3 `signedout` and `anon` each give exit 3, and stderr contains `gemini-web login`. `sorry` gives exit 1 with "blocked".
- AC-4 `hang` with `--timeout 5` gives exit 4 within 20 s, and the screenshot path printed on stderr exists.
- AC-5 `notextbox` gives exit 1, and stderr names the selector key. After a later successful run, `last-failure.*` are gone.
- AC-6 `--model auto` clicks the "Auto" item (the fake page echoes the chosen model). `--model zzz` gives exit 2 and lists the item titles.
- AC-7 The temporary-chat button is clicked by default. `notemp` gives the warning on stderr and exit 0.
- AC-8 removed (critic round 1, finding 3).
- AC-9 Two runs started at once both exit 0, one after the other. The lock helper with a 1 s wait times out to exit 1 while the test holds the lock, with the stderr message `another gemini-web run held the lock for N s; try again later`.
- AC-10 No focus change: the frontmost app (`lsappinfo front`, which needs no Automation permission), sampled every 250 ms before, during and after an `ask`, never changes.
- AC-11 The login argv builder (unit test, no launch) contains `--user-data-dir=<profile>` and `--use-mock-keychain`, and has no `--enable-automation`/`--remote-debugging`. The headless launch args contain `--use-mock-keychain` and `--password-store=basic`, and `ignore_default_args` contains `--enable-automation`.

### CP2: image
- AC-12 `image "a cube" -o <new dir>` creates the directory, saves a file with the same bytes as the 2048x2048 fixture (the full size, not the 512 preview), prints its absolute path, and exits 0.
- AC-13 `--json` prints one valid JSON line with `paths` (list) and `text`.
- AC-14 removed (no URL-rewrite fallback; see section 6).
- AC-15 `noimage` gives exit 1, the reply text on stderr, and no files written.
- AC-16 Two runs into the same directory give two different files. Nothing is overwritten.

### CP3: install, skill, LIVE
- AC-17 `uv tool install --editable .` puts `~/.local/bin/gemini-web` on PATH. `gemini-web --help` exits 0.
- AC-18 `~/.claude/skills/gemini-web/SKILL.md` has `name`/`description` frontmatter. It covers when to use the tool (second opinion, image generation), example commands, how to handle each exit code (exit 3: ask the user to run login, Claude does not run it; exit 1 or 4: read the stderr line and retry at most once), the Bash tool timeout to use (600000 ms, so that a run waiting up to 300 s for the lock and then running up to 240 s still finishes inside it), that a prompt starting with `-` must be passed after `--` (`gemini-web ask -- "-v vs --verbose?"`) or through stdin with `ask -`, that `last-failure.html` can hold the prompt, the reply and page tokens, limits (10-90 s per call, runs one at a time, no attachments, plain-text answers), and privacy (never send secrets or private user data to a consumer Google service).
- LIVE-1 After `gemini-web login`: `gemini-web ask "Reply with exactly: PONG"` prints `PONG` and exits 0, with no window and no focus change. (CP0 has already settled headless versus the fallback.)
- LIVE-2 That chat does not show in the user's Gemini history (the user checks in their normal browser), or the temporary-chat warning was printed.
- LIVE-3 `gemini-web image "a red cube on a white background" -o /tmp/gw` saves at least 1 file. `sips -g pixelWidth` reports 1024 or more, and the file is larger than the on-page preview.
- LIVE-4 A real `--model` name switches the model. A bad name lists the real names.
- LIVE-5 A multi-paragraph answer with a code block prints intact, with no UI chrome ("Show thinking", "Gemini can make mistakes").
- LIVE-7 A multi-line prompt piped to `ask -` arrives as one message, not split into several sends at the newlines.
- LIVE-6 LIVE-1 still passes 24 h or more after login with no new login (checks how long the session lasts).

## 8. Install

```
cd .
uv tool install --editable .        # ~/.local/bin/gemini-web
gemini-web login                    # one time, interactive, by the user
gemini-web ask "Reply with exactly: PONG"
```
No `playwright install` is needed, because the tool uses the installed Google Chrome.

## 9. Risks

1. **Headless detection** (highest; settled early by CP0). Google may serve a sign-in wall or a "sorry" page to headless Chrome. The sec-ch-ua client hints may still say HeadlessChrome after the user agent override. Mitigation: LIVE-1 gate and the `open -g` + CDP fallback.
2. **Mock keychain = weak encryption at rest (security).** Cookies in `profile/` are encrypted with Chromium's fixed, public mock key. Any process running as the same macOS user can read them and take over the whole Google account session (Gmail, Drive and so on), not only Gemini. Mode 700 only stops *other* macOS users. If the user does not need their paid Gemini plan for this, they should sign in with a secondary Google account. To revoke: delete the folder and sign out that session from the Google account's device page.
3. Session expiry: Chrome refreshes the session itself on every run. If Google ends it, the tool exits 3 and the user logs in again. LIVE-6 measures this.
4. UI drift: all selectors live in one dict, and failures save a screenshot and the HTML. Expect to fix a selector now and then.
5. Terms of service and account risk: automating the consumer app may break Google's terms. Keep usage low-volume and personal, and say so in SKILL.md.
6. Image quotas on the user's plan show up as exit 1 ("no image") with Gemini's own text. For `ask`, quota text is printed as the reply.
7. Temporary-chat UI may be missing or may block image generation. The tool warns and continues. LIVE-2 and LIVE-3 confirm.
8. Chrome auto-updates can change headless behaviour. Re-run LIVE-1 after major Chrome updates.

Skipped on purpose: `--file` attachments, markdown rebuilding, multi-turn chats, a `status` command, a `--keep` history option, retries inside the tool.
