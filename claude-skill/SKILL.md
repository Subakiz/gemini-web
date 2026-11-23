---
name: gemini-web
description: Use the Gemini web app from the shell via the `gemini-web` CLI. Use it when the user asks to generate or draw an image or picture, says "ask Gemini", or wants a second opinion from another model on a topic, plan, design or piece of code. It generates new raster images only. Not for diagrams, charts, SVG or plots, image editing or analysis, or tasks Claude can do itself.
---

# gemini-web

CLI for the user's own Gemini web account (headless Chrome, no API key). Two jobs: a text second opinion, and image generation. Chats are temporary by default (not in the user's Gemini history). If stderr says the temporary chat button was not found, the chat IS saved to history: tell the user.

## Commands
```
gemini-web ask "Is a B-tree or LSM tree better for write-heavy logs?"
gemini-web ask -- "-v vs --verbose: which flag name is better?"   # prompt starting with "-": use --
gemini-web ask - <<'EOF'                                           # long prompts, code, multi-line: stdin
Review this function for bugs. Context: <what it does, constraints>.
<code here>
EOF
gemini-web ask --model auto "..."       # optional: first mode-menu item whose title contains the text
gemini-web image "a red cube on a white background" -o /tmp/gw        # prints one absolute path per image
gemini-web image "..." -o /tmp/gw --json                              # {"paths": [...], "text": "..."}
gemini-web image -o /tmp/gw -- "-prompt starting with a dash"         # leading dash: -o first, then --
```
- stdout carries only the result (answer text, or file paths). Progress and errors go to stderr, prefixed `gemini-web:`.
- Images are saved full size. After `image`, use the Read tool on each printed path to look at it. If the image is for a project, save it in the user's project dir, not /tmp.
- Defaults: `--timeout` 180 s for ask, 240 s for image. If you raise `--timeout`, keep lock wait + timeout + download under 600 s.

## How to run it
- Bash tool timeout: set 600000 ms. A call can wait up to 300 s for the lock, then run up to 240 s, then download.
- Normal call takes 10-90 s. One run at a time: concurrent calls queue on a lock, so do not start several in parallel.
- No attachments. Answers come back as plain text (no markdown syntax rebuilt).

## Exit codes
| Exit | Meaning | Do |
|---|---|---|
| 0 | ok | use stdout |
| 1 | failure; stderr says which (UI element not found, no image returned, blocked by Google, Chrome missing, busy lock) | read the stderr line. Blocked by Google or Chrome missing: do not retry, stop and tell the user. Busy lock or UI hiccup: retry at most once. "No image": Gemini's own reply is in the message, show it to the user, do not loop |
| 2 | usage error or unknown `--model` | fix the command; the error lists valid model names |
| 3 | not logged in | stop and ask the user to run `! gemini-web login` (opens a Chrome window; they sign in, then Cmd-Q). Never run login yourself |
| 4 | timeout | retry at most once, maybe with a shorter prompt |

Only UI, timeout and blocked failures write `last-failure.png` and `last-failure.html` (in `~/Library/Application Support/gemini-web/`; stderr names the screenshot path). The HTML holds the prompt, the reply and session tokens: never Read or cat it. Report the stderr line only.

## Using the answer
- Give Gemini full context: it sees only the prompt, not this conversation or the repo. Include the goal, constraints, relevant code and what you already tried.
- Treat the reply as a peer opinion, not ground truth. Verify claims (run the code, check docs) before acting, and tell the user what came from Gemini.
- If Gemini's reply is a quota or rate-limit message, `ask` still exits 0: read the text.

## Privacy and limits
- This is a consumer Google service on the user's personal account. Never send secrets (keys, tokens, passwords, .env contents) or private user data (messages, finances, personal files). Send unreleased or proprietary repo code only if the user okays it.
- Keep usage low-volume: automating the consumer app may break Google's terms.
- Image quotas follow the user's plan; hitting one shows up as exit 1 with Gemini's text.
- Maintenance: DOM selectors live in one dict (`SELECTORS` in `<repo>/gemini_web/web.py`); a Gemini UI change is a one-line fix there, but if the UI breaks, tell the user; do not edit web.py unasked.
