"""Manual driver: run the agent's browser tools one call at a time, without any LLM.

Lets a human (or an outside assistant) play the role of the model — useful for
debugging the tool layer and for demos without spending API money.

    python drive.py launch                     # start visible Chrome with CDP on :9222
    python drive.py snapshot
    python drive.py click '{"ref": "12"}'
    python drive.py type '{"ref": "3", "text": "бургер", "submit": true}'
    python drive.py extract '{"question": "..."}'   # prints full page text instead of calling a model

Each call attaches to the running Chrome over CDP, does one action, prints the
fresh snapshot and detaches. Element ids live in the page DOM, so they persist
between calls. The security guard is NOT applied here: the human decides.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agent.browser import ActionError, BrowserSession

CDP = "http://localhost:9222"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def launch() -> None:
    profile = Path(__file__).parent / ".profile-cdp"
    subprocess.Popen([CHROME, "--remote-debugging-port=9222", f"--user-data-dir={profile}",
                      "--no-first-run", "--no-default-browser-check", "about:blank"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    print(f"Chrome launched with CDP at {CDP}")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "launch":
        return launch()
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    b = BrowserSession(cdp_url=CDP)
    b.start()
    # Attach to the tab the user is looking at (the last one), not a random first tab.
    b.page = b.context.pages[-1]
    try:
        match cmd:
            case "navigate": b.navigate(args["url"])
            case "click": b.click(args["ref"])
            case "type": b.type_text(args["ref"], args["text"], bool(args.get("submit")))
            case "select_option": b.select(args["ref"], args["option"])
            case "press_key": b.press(args["key"])
            case "scroll": b.scroll(args.get("direction", "down"), args.get("ref"))
            case "go_back": b.go_back()
            case "switch_tab": b.switch_tab(int(args["index"]))
            case "extract":
                print(b.page_text(20_000))
                return
            case "snapshot": pass
            case _: sys.exit(f"unknown command {cmd}")
        print(b.snapshot())
    except ActionError as e:
        print(f"ERROR: {e}\n\nCurrent page:\n{b.snapshot()}")
    finally:
        b.close()


if __name__ == "__main__":
    main()
