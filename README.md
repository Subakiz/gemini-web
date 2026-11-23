# gemini-web

> [!WARNING]
> **This tool may violate Google's Terms of Service.** It automates the consumer Gemini web app (gemini.google.com) with a scripted browser, which Google may treat as unauthorized automated access. Your account could be rate-limited, flagged or suspended. **Use a throwaway Google account**, never your main one. The saved login profile also holds a full Google session (see Security note below). Use at your own risk.

Small CLI that lets Claude Code (or you) use the Gemini web app from a shell: a text second opinion, or image generation saved at full resolution. It drives your real Google Chrome headless with a dedicated profile, so no API key is needed and it never takes window focus.

## Install
```
uv tool install -e .                  # run from the repo root; puts gemini-web in ~/.local/bin
gemini-web login                                      # once; sign in in the window, then Cmd-Q
```
Needs Google Chrome in /Applications. State lives in `~/Library/Application Support/gemini-web/` (mode 700).

## Use
```
gemini-web ask "question"            # or: gemini-web ask - <<'EOF' ... EOF
gemini-web ask --model auto "question"
gemini-web image "prompt" -o DIR [--json]
```
Exit codes: 0 ok, 1 failure (see stderr), 2 usage or unknown model, 3 not logged in, 4 timeout.
Claude usage notes are in `~/.claude/skills/gemini-web/SKILL.md`. Design and acceptance criteria: `docs/PLAN.md`.

## Test
```
uv run --with pytest pytest -q       # offline, against tests/fake_gemini.html; about 5 minutes
```

## Security note
The login profile uses Chromium's mock keychain, so its cookies are encrypted with a fixed, publicly known key. Any process running as your macOS user can read them and take over the whole Google session. Time Machine backups copy `profile/` and `last-failure.*`. Prefer a secondary Google account. To revoke, delete the state folder and sign the session out from your Google account's device page.
