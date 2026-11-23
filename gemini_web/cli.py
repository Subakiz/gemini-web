import argparse
import json
import sys
from pathlib import Path

from . import web


def main():
    ap = argparse.ArgumentParser(prog="gemini-web", description="Use the Gemini web app from a shell.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="ask Gemini; the answer goes to stdout")
    a.add_argument("prompt", nargs="?", help='prompt text; "-" or absent with stdin piped = read stdin')
    a.add_argument("--model", help="substring of a mode-menu item title, e.g. Auto")
    a.add_argument("--timeout", type=int, default=180, help="seconds to wait for a complete reply")
    i = sub.add_parser("image", help="generate an image; full-size file paths go to stdout")
    i.add_argument("prompt")
    i.add_argument("-o", "--out", required=True, help="directory to save into (created if needed)")
    i.add_argument("--json", action="store_true", help='print one line: {"paths": [...], "text": "..."}')
    i.add_argument("--timeout", type=int, default=240, help="seconds to wait for a complete reply")
    sub.add_parser("login", help="one-time interactive sign-in (visible Chrome window)")
    args = ap.parse_args()
    try:
        if args.cmd == "login":
            web.login()
            return
        if args.cmd == "image":
            if not args.prompt.strip():
                ap.error("empty prompt")
            paths, text = web.session(lambda page: web.image(page, args.prompt, Path(args.out).expanduser().resolve(), args.timeout))
            print(json.dumps({"paths": paths, "text": text}) if args.json else "\n".join(paths))
            return
        prompt = args.prompt
        if prompt in (None, "-"):
            if prompt is None and sys.stdin.isatty():
                ap.error("no prompt given")
            prompt = sys.stdin.read()
        if not prompt.strip():
            ap.error("empty prompt")
        print(web.session(lambda page: web.ask(page, prompt, args.timeout, args.model)))
    except Exception as e:  # SystemExit from web.die passes through untouched
        web.die(1, f"internal error: {e!r}")


if __name__ == "__main__":
    main()
