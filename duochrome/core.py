"""DuoChrome — the main API.

Usage (library):
    from duochrome import DuoChrome
    dc = DuoChrome()                              # default ~/.duochrome
    dc.init()
    p = dc.create("门店A-账号1", group="门店A")
    ctx = dc.launch(p)                            # returns playwright BrowserContext
    page = ctx.pages[0]
    page.goto("https://example.com")
    ...
    ctx.close()                                   # kills that Chromium

Usage (CLI):
    duochrome init
    duochrome create 门店A-账号1 --group 门店A
    duochrome ls
    duochrome launch 门店A-账号1 --url https://example.com
    duochrome launch --group 门店A                 # batch
    duochrome stop 门店A-账号1
    duochrome rm 门店A-账号1
"""
from __future__ import annotations

import os
import os as _os  # alias used by launch_group's subprocess env (kept separate to avoid shadowing `os`)

from pathlib import Path
from typing import Iterable, Optional

from playwright.sync_api import BrowserContext

from duochrome import chrome as chrome_mod
from duochrome.profile import Profile, ProfileStore


_DEFAULT_ROOT = Path.home() / ".duochrome"


class ProfileAlreadyRunning(Exception):
    """Raised when launch() is called on a profile whose .pid is still alive."""


class DuoChrome:
    def __init__(self, root: Optional[Path] = None):
        # Precedence: explicit arg > $DUOCHROME_ROOT env > ~/.duochrome
        if root is None:
            env_root = os.environ.get("DUOCHROME_ROOT")
            if env_root:
                root = Path(env_root)
        self.root = Path(root).expanduser().resolve() if root else _DEFAULT_ROOT
        self.store = ProfileStore(self.root)

    # ---------- lifecycle ----------

    def init(self) -> None:
        self.store.init()

    # ---------- profile CRUD ----------

    def create(
        self,
        name: str,
        *,
        group: str = "default",
        proxy: Optional[str] = None,
        user_agent: Optional[str] = None,
        viewport: tuple[int, int] = (1280, 800),
        notes: str = "",
    ) -> Profile:
        p = Profile(
            name=name,
            group=group,
            proxy=proxy,
            user_agent=user_agent,
            viewport_w=viewport[0],
            viewport_h=viewport[1],
            notes=notes,
        )
        return self.store.create(p)

    def list(self, group: Optional[str] = None) -> list[Profile]:
        items = self.store.list()
        if group:
            items = [p for p in items if p.group == group]
        return items

    def get(self, name: str) -> Optional[Profile]:
        return self.store.get(name)

    def remove(self, name: str) -> bool:
        return self.store.remove(name)

    # ---------- launch / stop ----------

    def launch(
        self,
        name: str,
        *,
        headless: bool = False,
        url: Optional[str] = None,
        force: bool = False,
    ) -> BrowserContext:
        p = self.store.get(name)
        if p is None:
            raise KeyError(f"profile '{name}' not found")
        if not force and self.store.is_alive(name):
            raise ProfileAlreadyRunning(
                f"profile '{name}' is already running (PID {self.store.read_pid(name)}). "
                "Pass force=True to ignore, or call stop() first."
            )
        ctx = chrome_mod.launch(p, root=self.root, headless=headless, url=url)
        self.store.update(p)
        return ctx

    def launch_group(
        self,
        group: str,
        *,
        headless: bool = False,
        url: Optional[str] = None,
        force: bool = False,
    ) -> list[int]:
        """Launch every profile in `group`.

        Playwright's sync API creates a per-process asyncio event loop, so launching
        multiple contexts sequentially in one process blows up on the 2nd call.
        Solution: each profile runs in its OWN detached subprocess (each with its
        own Playwright instance). We `subprocess.Popen` a fresh Python process
        per profile; that process stays alive holding the Chromium window.

        Returns: list of wrapper PIDs (one per launched subprocess). The wrapper
        is what you kill with `kill <pid>`; the Chromium it spawned will exit
        when the wrapper exits.
        """
        pids: list[int] = []
        for p in self.list(group=group):
            try:
                pids.append(self._launch_subprocess(p.name, headless=headless, url=url, force=force))
            except ProfileAlreadyRunning as e:
                print(f"[skip] {e}")
        return pids

    def _launch_subprocess(
        self,
        name: str,
        *,
        headless: bool = False,
        url: Optional[str] = None,
        force: bool = False,
    ) -> int:
        """Spawn a detached subprocess that runs `duochrome launch <name>`.

        Returns the wrapper PID. The Chromium lives inside that subprocess and
        exits when the wrapper is killed (SIGTERM/SIGKILL). Used by both
        `launch_group` and the Web UI — the CLI's own `duochrome launch <name>`
        instead runs in the foreground (blocks until Ctrl+C).

        Raises:
            KeyError: profile doesn't exist
            ProfileAlreadyRunning: pidfile exists and process is alive (unless force=True)
        """
        import os as _os
        import subprocess
        import sys as _sys

        if not self.store.exists(name):
            raise KeyError(f"profile '{name}' not found")
        if self.store.is_alive(name) and not force:
            raise ProfileAlreadyRunning(
                f"profile '{name}' is already running (PID {self.store.read_pid(name)})"
            )

        args = [_sys.executable, "-m", "duochrome.cli", "launch", name]
        if headless:
            args.append("--headless")
        if url:
            args += ["--url", url]
        if force:
            args.append("--force")

        env = {**_os.environ, "DUOCHROME_ROOT": str(self.root)}

        log_dir = self.root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{name}.log"
        log_fp = open(log_path, "ab", buffering=0)

        proc = subprocess.Popen(
            args,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_fp,
            stderr=log_fp,
            start_new_session=True,
            close_fds=True,
        )
        return proc.pid

    def stop(self, name: str) -> bool:
        """Kill the Chromium process whose PID we tracked. Returns True if killed."""
        pid = self.store.read_pid(name)
        if pid is None:
            return False
        try:
            os.kill(pid, 9)
        except (OSError, ProcessLookupError):
            pass
        self.store.clear_pid(name)
        # mark profile as not alive
        p = self.store.get(name)
        if p is not None:
            p.last_pid = None
            self.store.update(p)
        return True

    def stop_all(self) -> int:
        killed = 0
        for p in self.store.list():
            if self.store.is_alive(p.name):
                self.stop(p.name)
                killed += 1
        return killed

    # ---------- inspection ----------

    def status(self, name: str) -> dict:
        p = self.store.get(name)
        if p is None:
            raise KeyError(name)
        return {
            "name": p.name,
            "group": p.group,
            "proxy": p.proxy,
            "ua": p.user_agent,
            "last_opened_at": p.last_opened_at,
            "alive": self.store.is_alive(name),
            "pid": self.store.read_pid(name),
            "chrome_dir": str(p.chrome_dir(self.root)),
        }