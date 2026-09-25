"""Profile metadata + on-disk layout.

Layout under <root>/:
    root/
        profiles.json        # index: {name: Profile-as-dict}
        profiles/
            <name>/
                chrome/      # Chromium user-data-dir (cookies, cache, IndexedDB, ...)
                .pid         # running PID (if launched)

Profile metadata is small (name/group/proxy/UA/last_opened) and lives in profiles.json
so listing is cheap. Real browser data lives in chrome/.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Profile:
    name: str
    group: str = "default"
    proxy: Optional[str] = None        # "http://host:port" or "socks5://host:port"
    user_agent: Optional[str] = None
    viewport_w: int = 1280
    viewport_h: int = 800
    notes: str = ""
    created_at: float = field(default_factory=time.time)
    last_opened_at: Optional[float] = None
    last_pid: Optional[int] = None    # PID of last launched chromium (best-effort)

    def chrome_dir(self, root: Path) -> Path:
        return root / "profiles" / self.name / "chrome"

    def pid_file(self, root: Path) -> Path:
        return root / "profiles" / self.name / ".pid"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        # tolerate unknown fields (forward-compat)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


class ProfileStore:
    """Tiny JSON-backed store. Single writer, no concurrency guarantees."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.index_path = self.root / "profiles.json"
        self.profiles_dir = self.root / "profiles"
        self._cache: dict[str, Profile] | None = None

    # ---------- lifecycle ----------

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._flush({})

    # ---------- CRUD ----------

    def _load(self) -> dict[str, Profile]:
        if self._cache is not None:
            return self._cache
        if not self.index_path.exists():
            self._cache = {}
            return self._cache
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
        self._cache = {name: Profile.from_dict(d) for name, d in raw.items()}
        return self._cache

    def _flush(self, data: dict[str, Profile]) -> None:
        payload = {name: p.to_dict() for name, p in data.items()}
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_path)
        self._cache = data

    def list(self) -> list[Profile]:
        return list(self._load().values())

    def get(self, name: str) -> Optional[Profile]:
        return self._load().get(name)

    def exists(self, name: str) -> bool:
        return name in self._load()

    def create(self, profile: Profile) -> Profile:
        data = self._load()
        if profile.name in data:
            raise FileExistsError(f"profile '{profile.name}' already exists")
        profile.chrome_dir(self.root).mkdir(parents=True, exist_ok=True)
        data[profile.name] = profile
        self._flush(data)
        return profile

    def update(self, profile: Profile) -> Profile:
        data = self._load()
        if profile.name not in data:
            raise KeyError(f"profile '{profile.name}' not found")
        data[profile.name] = profile
        self._flush(data)
        return profile

    def remove(self, name: str) -> bool:
        data = self._load()
        if name not in data:
            return False
        # wipe on-disk data
        pdir = self.profiles_dir / name
        if pdir.exists():
            import shutil
            shutil.rmtree(pdir)
        del data[name]
        self._flush(data)
        return True

    # ---------- PID tracking (best-effort) ----------

    def write_pid(self, name: str, pid: int) -> None:
        pidfile = self.profiles_dir / name / ".pid"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(str(pid), encoding="utf-8")

    def read_pid(self, name: str) -> Optional[int]:
        pidfile = self.profiles_dir / name / ".pid"
        if not pidfile.exists():
            return None
        try:
            return int(pidfile.read_text(encoding="utf-8").strip())
        except ValueError:
            return None

    def clear_pid(self, name: str) -> None:
        pidfile = self.profiles_dir / name / ".pid"
        try:
            pidfile.unlink()
        except FileNotFoundError:
            pass

    def is_alive(self, name: str) -> bool:
        pid = self.read_pid(name)
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False