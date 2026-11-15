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
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, sync_playwright

from duochrome.fingerprint import stealth_script
from duochrome.profile import Profile


class BrowserNotFoundError(RuntimeError):
    """No usable Chromium binary could be found (and none could be installed).

    Raised instead of letting Playwright's raw "Executable doesn't exist at ..."
    traceback leak to the user — that message points at an internal bundle path
    and gives no actionable next step.
    """


@dataclass
class BrowserTarget:
    """A resolved Chromium to launch.

    Either `executable_path` (explicit binary) or `channel` (Playwright-managed
    lookup such as "chrome" for the system-installed Google Chrome) is set.
    """

    executable_path: Optional[str] = None
    channel: Optional[str] = None
    source: str = ""


def _browser_search_roots() -> list[Path]:
    """Every place a Chromium binary may live, most likely first.

    1. `~/Library/Caches/ms-playwright` — where `playwright install` puts it.
       ⚠️ This is a *cache* directory: many cleanup tools (CleanMyMac, 腾讯
       柠檬, 系统存储管理…) wipe `~/Library/Caches` wholesale, which silently
       breaks every Playwright-based app on the machine. Hence the fallbacks.
    2. Inside the PyInstaller bundle (if the user installed it there).
    3. Inside the pip package's own driver dir (venv-local installs).
    """
    roots: list[Path] = []

    # Playwright's own override — when set it means "the browsers live HERE and
    # nowhere else" (same semantics as the driver), so we don't fall back to
    # the default cache location.
    env_cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_cache:
        roots.append(Path(env_cache).expanduser())
    else:
        roots.append(Path.home() / "Library" / "Caches" / "ms-playwright")

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass) / "playwright" / "driver" / "package" / ".local-browsers")

    try:
        import playwright as _pw_pkg

        roots.append(
            Path(_pw_pkg.__file__).resolve().parent / "driver" / "package" / ".local-browsers"
        )
    except Exception:  # pragma: no cover - playwright is a hard dependency
        pass

    return roots


def _find_chromium_executable(headless: bool = False) -> Optional[str]:
    """Locate the Chromium binary already installed via `playwright install`.

    When PyInstaller bundles the .app, Playwright's internal logic still
    looks for the browser inside the bundle's Resources/playwright/...
    path — which doesn't exist (Chromium isn't part of the wheel, it must
    be installed separately). PLAYWRIGHT_BROWSERS_PATH only affects install
    location, not runtime lookup, so we have to point Playwright at the
    system cache directly via `executable_path`.

    Picks the right binary by intent:
      - headless=True  → chrome-headless-shell (no GUI, lighter)
      - headless=False → "Google Chrome for Testing" / Chromium (real window)

    Returns None if neither is found.
    """
    import glob as _glob

    headed_patterns = [
        # playwright >= 1.49 on macOS arm64: chrome-mac-arm64/Google Chrome for Testing.app
        "chromium-*/chrome-mac-*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        # playwright <= 1.48: chrome-mac/Chromium.app
        "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-mac-*/Chromium.app/Contents/MacOS/Chromium",
    ]
    headless_patterns = [
        "chromium_headless_shell-*/chrome-headless-shell-mac-*/chrome-headless-shell",
        "chromium_headless_shell-*/chrome-headless-shell-mac/chrome-headless-shell",
    ]

    patterns = headless_patterns if headless else headed_patterns

    for root in _browser_search_roots():
        for pattern in patterns:
            matches = sorted(_glob.glob(str(root / pattern)))
            if matches:
                return matches[0]

    if not headless:
        # Last resort for headed mode: headless-shell can still run with
        # --headless=new but shows NO GUI window — only use if nothing else
        # exists, and say so in the source label.
        for root in _browser_search_roots():
            for pattern in headless_patterns:
                matches = sorted(_glob.glob(str(root / pattern)))
                if matches:
                    return matches[0]
    return None


def _find_system_chrome() -> Optional[str]:
    """Locate a system-installed Google Chrome / Chromium.

    Used as a last-resort browser when the Playwright cache has been wiped:
    the user's own Chrome is always present and needs no download.
    """
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        str(Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"),
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def resolve_browser(headless: bool = False) -> Optional[BrowserTarget]:
    """Resolve which Chromium to launch, trying every known source in order.

    Order:
      1. Playwright cache (bundled "Chrome for Testing" / headless-shell)
      2. System-installed Google Chrome (`channel="chrome"`)

    Returns None when nothing is available.
    """
    exe = _find_chromium_executable(headless=headless)
    if exe:
        return BrowserTarget(executable_path=exe, source="playwright cache")

    sys_chrome = _find_system_chrome()
    if sys_chrome:
        return BrowserTarget(channel="chrome", source=sys_chrome)

    return None


def missing_browser_message() -> str:
    """Actionable error text shown when no Chromium can be found."""
    return (
        "找不到可用的 Chromium 浏览器内核。\n"
        " duoChrome 需要 Chromium 才能启动窗口，它安装在 "
        "~/Library/Caches/ms-playwright/ —— 这是系统缓存目录，"
        "被清理工具删掉就会变成这样。\n"
        "\n"
        "修复（任选其一）：\n"
        "  1) 打开终端执行：duochrome doctor --install\n"
        "     （会自动下载并装好匹配版本的 Chromium，约 170MB）\n"
        "  2) 或手动执行：python3 -m playwright install chromium\n"
        "  3) 如果你不介意用系统已装的 Chrome，也可以直接启动：\n"
        "     系统 Chrome 会被作为兜底内核自动使用。"
    )


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

    # First pass: find every Chromium-family PID (headed "Google Chrome for
    # Testing" + "Chromium" + headless "chrome-headless-shell"). They all
    # have `--user-data-dir` so we can also distinguish per-profile later.
    chromium_pids: set[int] = set()
    for line in out.splitlines():
        if not any(s in line for s in (
            "chrome-headless-shell",
            "Google Chrome for Testing",
            "Chromium.app/Contents/MacOS/Chromium",
            "/Chromium --",
        )):
            continue
        try:
            chromium_pids.add(int(line.split(None, 1)[0]))
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
                if c in chromium_pids:
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
    # Pick headed vs headless binary by intent — the headed "Chrome for
    # Testing" bundle is required to actually show a window.
    target = resolve_browser(headless=headless)
    if target is None:
        raise BrowserNotFoundError(missing_browser_message())
    if target.executable_path:
        launch_kwargs["executable_path"] = target.executable_path
    elif target.channel:
        launch_kwargs["channel"] = target.channel

    pw = sync_playwright().start()
    try:
        ctx = pw.chromium.launch_persistent_context(**launch_kwargs)
    except Exception as e:
        # Playwright's own error is a giant traceback pointing at an internal
        # bundle path ("Executable doesn't exist at .../playwright/driver/...")
        # — useless to the user. Translate it into something actionable.
        msg = str(e)
        if "doesn't exist" in msg or "Executable doesn't exist" in msg:
            raise BrowserNotFoundError(missing_browser_message()) from e
        raise

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