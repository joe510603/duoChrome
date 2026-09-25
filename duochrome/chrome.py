"""Chrome launch wrapper.

Every `launch_persistent_context` call:
  - spawns a fresh OS-level Chromium process (independent from other profiles)
  - binds to a unique user_data_dir on disk (cookies / cache / IndexedDB isolated)
  - applies a small stealth init script (see fingerprint.py)

The returned BrowserContext is a live handle — closing it kills the Chromium process.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, sync_playwright

from .fingerprint import stealth_script
from .profile import Profile


def _find_chromium_pid(user_data_dir: str) -> Optional[int]:
    """Best-effort: locate the Chromium process spawned for `user_data_dir`.

    Playwright's Python API doesn't expose process PID directly across all
    versions, so we shell out to `ps` and filter by the full user-data-dir path.
    Returns the PID of the first matching chromium-like process, or None.

    Note: we match the FULL user-data-dir path so that other Chromium-based
    apps (Chrome/Edge/Adobe CEF/WPS) running on the host don't pollute the
    result. The full path is unique per duoChrome profile.
    """
    try:
        out = subprocess.check_output(
            ["ps", "-axo", "pid=,command="], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    # Prefer chrome-headless-shell (Playwright default for headless). If absent,
    # fall back to any chromium / Google Chrome binary.
    candidates: list[tuple[int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or user_data_dir not in line:
            continue
        if not any(b in line for b in ("chrome-headless-shell", "Google Chrome", "/chromium", "chrome ")):
            continue
        try:
            pid = int(line.split(None, 1)[0])
        except (ValueError, IndexError):
            continue
        # Skip the launcher subprocess itself (Playwright wrapper). The launcher
        # is short-lived; the actual Chromium process lives much longer.
        if "chrome-headless-shell" in line:
            candidates.insert(0, (pid, line))  # headless shell = the real browser
        else:
            candidates.append((pid, line))
    return candidates[0][0] if candidates else None


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