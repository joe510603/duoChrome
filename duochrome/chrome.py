"""Chrome launch wrapper.

Every `launch_persistent_context` call:
  - spawns a fresh OS-level Chromium process (independent from other profiles)
  - binds to a unique user_data_dir on disk (cookies / cache / IndexedDB isolated)
  - applies a small stealth init script (see fingerprint.py)
  - ensures the bookmarks bar is visible (writes Preferences JSON before launch)

The returned BrowserContext is a live handle — closing it kills the Chromium process.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, sync_playwright

from duochrome.fingerprint import stealth_script
from duochrome.profile import Profile


def _find_chromium_executable() -> Optional[str]:
    """Locate the Chromium binary the user already has via `playwright install`.

    When PyInstaller bundles the .app, Playwright's internal logic still
    looks for the browser inside the bundle's Resources/playwright/...
    path — which doesn't exist (Chromium isn't part of the wheel, it must
    be installed separately). PLAYWRIGHT_BROWSERS_PATH only affects install
    location, not runtime lookup, so we have to point Playwright at the
    system cache directly via `executable_path`.

    Returns the path to chrome-headless-shell if found, else None.
    """
    import glob as _glob

    pattern = str(
        Path.home()
        / "Library"
        / "Caches"
        / "ms-playwright"
        / "chromium_headless_shell-*"
        / "chrome-headless-shell-mac-*"
        / "chrome-headless-shell"
    )
    matches = sorted(_glob.glob(pattern))
    return matches[0] if matches else None


def _find_chromium_pid(user_data_dir: str) -> Optional[int]:
    """Best-effort: locate the Chromium process spawned for `user_data_dir`.

    Implementation: look for chrome-headless-shell whose parent process is
    the Playwright Node driver — that's the one this wrapper just started.

    Earlier versions matched on `user_data_dir in command line`, which broke
    when profile names contain non-ASCII characters: `ps` decodes UTF-8 bytes
    as latin-1 (`M-hM-/M-hM-/M^U` for "试试1"), so the literal Python string
    never matches. Matching by PPID is encoding-agnostic and uniquely ties
    the chromium process back to our wrapper.
    """
    import os

    try:
        # `pgrep -P <ppid>` returns all child PIDs of <ppid>. Our wrapper
        # spawned the Playwright Node driver; chromium is a grandchild.
        out = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid=,command="], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    # First pass: find all chrome-headless-shell PIDs
    shell_pids: set[int] = set()
    for line in out.splitlines():
        line = line.strip()
        if "chrome-headless-shell" not in line:
            continue
        try:
            shell_pids.add(int(line.split(None, 1)[0]))
        except (ValueError, IndexError):
            continue

    # Second pass: find the Playwright Node driver PPID chain that leads
    # from our wrapper down to one of those shell PIDs.
    # Build PPID → PID map, then walk from our pid downward.
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0]); ppid = int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)

    # BFS from this process down, stop at the first chrome-headless-shell
    frontier = [os.getpid()]
    seen: set[int] = set()
    while frontier:
        next_frontier = []
        for p in frontier:
            for c in children.get(p, []):
                if c in seen:
                    continue
                seen.add(c)
                if c in shell_pids:
                    return c
                next_frontier.append(c)
        frontier = next_frontier
    return None


def launch(profile: Profile, *, root: Path, headless: bool = False, url: Optional[str] = None) -> BrowserContext:
    """Launch an isolated Chromium for `profile`.

    Args:
        profile: the profile to launch.
        root:    duoChrome data root (ProfileStore.root).
        headless: run without UI window.
        url:     optional URL to open immediately after launch.

    Returns:
        A live playwright BrowserContext. Caller is responsible for closing it.
    """
    user_data_dir = profile.chrome_dir(root)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    # Always show the bookmarks bar — write Preferences BEFORE Chrome starts.
    # Chrome reads this file on launch; we respect existing user choices (if they
    # explicitly disabled the bar, leave it alone).
    _ensure_bookmark_bar(user_data_dir)

    launch_kwargs: dict = dict(
        headless=headless,
        user_data_dir=str(user_data_dir),
        viewport={"width": profile.viewport_w, "height": profile.viewport_h},
        args=[
            "--disable-blink-features=AutomationControlled",
            f"--window-name=duochrome-{profile.name}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )
    if profile.user_agent:
        launch_kwargs["user_agent"] = profile.user_agent
    if profile.proxy:
        # Playwright proxy format: {"server": "host:port", "username?": ..., "password?": ...}
        launch_kwargs["proxy"] = _parse_proxy(profile.proxy)

    # When bundled by PyInstaller, Playwright can't find Chromium via its
    # default bundle-relative lookup. Point it at the system cache.
    executable = _find_chromium_executable()
    if executable:
        launch_kwargs["executable_path"] = executable

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(**launch_kwargs)

    # Stealth patch — apply to every new page before any user script runs
    ctx.add_init_script(stealth_script())

    # Track PID (best-effort). Allow a brief grace period for the OS process to register.
    time.sleep(0.2)
    pid = _find_chromium_pid(str(user_data_dir))
    if pid is not None:
        profile.last_pid = pid
        profile.last_opened_at = time.time()
        (root / "profiles" / profile.name / ".pid").write_text(
            str(pid), encoding="utf-8"
        )

    if url:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url)

    return ctx


def _parse_proxy(url: str) -> dict:
    """Convert a proxy URL string to Playwright's proxy dict.

    Supports:
      http://host:port
      https://host:port
      socks5://host:port
      http://user:pass@host:port
    """
    from urllib.parse import urlparse

    p = urlparse(url)
    server = f"{p.hostname}:{p.port}" if p.port else (p.hostname or "")
    out = {"server": server}
    if p.username:
        out["username"] = p.username
    if p.password:
        out["password"] = p.password
    # Playwright auto-detects scheme from URL prefix
    if p.scheme.startswith("socks"):
        out["server"] = f"{p.scheme}://{server}"
    return out


def _ensure_bookmark_bar(user_data_dir: Path) -> None:
    """Force Chrome to show the bookmarks bar on launch.

    Writes `<user_data_dir>/Default/Preferences` (merged with any existing
    JSON the user has there) so that:
      bookmark_bar.show_on_all_tabs = true

    Behavior:
    - First launch of a fresh profile: key gets set to true (default).
    - User later disabled the bar in Chrome UI: we leave it disabled
      (show_on_all_tabs is explicitly false → respect that choice).
    - If the file is missing or corrupt: we start from an empty dict.
    """
    prefs_path = user_data_dir / "Default" / "Preferences"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if prefs_path.exists():
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        else:
            prefs = {}
    except (json.JSONDecodeError, OSError):
        prefs = {}

    bookmark_bar = prefs.setdefault("bookmark_bar", {})
    # Only set true if user hasn't explicitly disabled it
    if bookmark_bar.get("show_on_all_tabs") is not False:
        bookmark_bar["show_on_all_tabs"] = True

    prefs_path.write_text(
        json.dumps(prefs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )