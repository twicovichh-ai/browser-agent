"""Start a real Chrome with a CDP port. The agent and the live viewer both attach to it."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

CDP_PORT = int(os.getenv("CDP_PORT", "9222"))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _chrome_binary() -> str:
    if os.getenv("CHROME_BIN"):
        return os.environ["CHROME_BIN"]
    if Path(MAC_CHROME).exists():
        return MAC_CHROME
    from playwright.sync_api import sync_playwright  # bundled Chromium (Docker image)
    with sync_playwright() as p:
        return p.chromium.executable_path


def cdp_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1) as r:
            return "webSocketDebuggerUrl" in json.loads(r.read())
    except OSError:
        return False


def launch_chrome(profile_dir: str) -> subprocess.Popen | None:
    """Launch Chrome unless one is already listening on the CDP port."""
    if cdp_ready():
        return None
    args = [
        _chrome_binary(),
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run", "--no-default-browser-check",
        "--window-position=0,0", f"--window-size={os.getenv('WINDOW_SIZE', '1280,860')}",
        "--lang=ru-RU",
    ]
    if os.getenv("IN_DOCKER"):
        args += ["--no-sandbox", "--disable-dev-shm-usage", "--start-maximized"]
    args.append("about:blank")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(60):
        if cdp_ready():
            return proc
        time.sleep(0.25)
    raise RuntimeError("Chrome did not open the CDP port in 15s")
