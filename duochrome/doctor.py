"""Environment doctor — diagnose and repair the Chromium runtime dependency.

The #1 failure mode for a packaged duoChrome .app is a *missing Chromium
binary*. Playwright keeps it in `~/Library/Caches/ms-playwright/`, which is a
cache directory — many cleanup tools wipe `~/Library/Caches` wholesale, and
the app then dies with an internal-looking "Executable doesn't exist at
.../playwright/driver/package/.local-browsers/..." traceback.

`duochrome doctor` reports exactly what is present and what is missing;
`duochrome doctor --install` repairs it by downloading the Chromium build that
matches the bundled Playwright driver (so the CDP protocol always matches).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from duochrome import chrome as chrome_mod


def _bundled_driver() -> Optional[tuple[Path, Path]]:
    """Locate the Playwright Node driver as (node_binary, cli.js).

    Prefers the PyInstaller bundle (so a packaged .app downloads the exact
    revision its own driver expects), then falls back to the pip package.
    """
    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "playwright" / "driver")

    try:
        import playwright as _pw_pkg

        candidates.append(Path(_pw_pkg.__file__).resolve().parent / "driver")
    except Exception:  # pragma: no cover
        pass

    for base in candidates:
        node = base / "node"
        cli = base / "package" / "cli.js"
        if node.exists() and cli.exists():
            return node, cli
    return None


def diagnose() -> dict:
    """Inspect the local Chromium runtime and return a structured report."""
    headed = chrome_mod._find_chromium_executable(headless=False)
    headless = chrome_mod._find_chromium_executable(headless=True)
    sys_chrome = chrome_mod._find_system_chrome()
    driver = _bundled_driver()

    cache_dir = Path.home() / "Library" / "Caches" / "ms-playwright"
    cache_exists = cache_dir.exists()

    # Which browser would actually be used for a normal (headed) launch?
    target = chrome_mod.resolve_browser(headless=False)

    issues: list[str] = []
    if headed is None and sys_chrome is None:
        issues.append("no-chromium")
    elif headed is None and sys_chrome is not None:
        issues.append("playwright-cache-missing-system-chrome-fallback")

    return {
        "ok": not issues or issues == ["playwright-cache-missing-system-chrome-fallback"],
        "issues": issues,
        "playwright_cache_dir": str(cache_dir),
        "playwright_cache_exists": cache_exists,
        "headed_browser": headed,
        "headless_browser": headless,
        "system_chrome": sys_chrome,
        "driver": str(driver[0].parent) if driver else None,
        "effective_browser": (
            (target.executable_path or f"channel={target.channel} ({target.source})")
            if target
            else None
        ),
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def install_browser() -> tuple[bool, str]:
    """Download the Chromium build matching the local Playwright driver.

    Returns (ok, message).
    """
    driver = _bundled_driver()
    if driver is None:
        return False, (
            "找不到 Playwright 的 Node driver，无法自动安装浏览器。\n"
            "请手动执行：python3 -m playwright install chromium"
        )

    node, cli = driver
    cmd = [str(node), str(cli), "install", "chromium"]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        return False, "下载超时（超过 30 分钟），请检查网络后重试。"
    except OSError as e:
        return False, f"启动安装进程失败：{e}"

    if proc.returncode != 0:
        tail = "\n".join((proc.stdout or "").splitlines()[-15:])
        return False, f"安装失败（退出码 {proc.returncode}）：\n{tail}"

    head = chrome_mod._find_chromium_executable(headless=False)
    if head is None:
        return False, (
            "安装命令执行完了，但仍然找不到 Chromium 二进制。\n"
            f"请检查目录：{Path.home() / 'Library' / 'Caches' / 'ms-playwright'}"
        )
    return True, f"✓ Chromium 已就绪：\n  {head}"
