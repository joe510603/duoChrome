"""FastAPI server for duoChrome Web UI.

Endpoints:
  GET    /                                  → HTML
  GET    /api/profiles?group=X              → list profiles
  POST   /api/profiles                      → create
  DELETE /api/profiles/{name}               → stop + remove
  POST   /api/profiles/{name}/launch        → spawn detached subprocess
  POST   /api/profiles/{name}/stop          → kill
  POST   /api/profiles/launch-group         → spawn N detached subprocesses
  POST   /api/profiles/stop-all             → kill everything alive
  GET    /api/status/{name}                 → detailed status
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from duochrome.core import DuoChrome, ProfileAlreadyRunning

app = FastAPI(title="duoChrome", version="0.1.0")

_STATIC_DIR = Path(__file__).parent / "static"


def _dc() -> DuoChrome:
    # DUOCHROME_ROOT env var is honored by DuoChrome() automatically.
    return DuoChrome()


# ---------- pydantic models ----------

class ProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    group: str = "default"
    proxy: Optional[str] = None
    ua: Optional[str] = None
    viewport_w: int = 1280
    viewport_h: int = 800
    notes: str = ""


class GroupLaunch(BaseModel):
    group: str
    headless: bool = False
    url: Optional[str] = None
    force: bool = False


class LaunchOptions(BaseModel):
    headless: bool = False
    url: Optional[str] = None
    force: bool = False


class ProfileUpdate(BaseModel):
    """Partial update — only supplied fields are changed."""
    group: Optional[str] = None
    proxy: Optional[str] = None
    ua: Optional[str] = None
    notes: Optional[str] = None
    viewport_w: Optional[int] = None
    viewport_h: Optional[int] = None


# ---------- routes ----------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = _STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/profiles")
async def list_profiles(group: Optional[str] = None):
    dc = _dc()
    dc.init()
    return [
        {
            "name": p.name,
            "group": p.group,
            "proxy": p.proxy,
            "ua": p.user_agent,
            "notes": p.notes,
            "viewport": [p.viewport_w, p.viewport_h],
            "alive": dc.store.is_alive(p.name),
            "pid": dc.store.read_pid(p.name),
            "last_opened_at": p.last_opened_at,
        }
        for p in dc.list(group=group)
    ]


@app.post("/api/profiles")
async def create_profile(body: ProfileCreate):
    dc = _dc()
    dc.init()
    try:
        dc.create(
            body.name,
            group=body.group,
            proxy=body.proxy,
            user_agent=body.ua,
            viewport=(body.viewport_w, body.viewport_h),
            notes=body.notes,
        )
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "name": body.name}


@app.delete("/api/profiles/{name}")
async def delete_profile(name: str):
    dc = _dc()
    if not dc.store.exists(name):
        raise HTTPException(404, f"profile '{name}' not found")
    # Stop first so we don't leave an orphan Chromium
    dc.stop(name)
    dc.remove(name)
    return {"ok": True, "name": name}


@app.patch("/api/profiles/{name}")
async def update_profile(name: str, body: ProfileUpdate):
    """Partial update — only fields the caller supplies are changed.

    Note: name itself is NOT editable here (rename would orphan on-disk data).
    Use delete + create if you really need to rename.
    """
    dc = _dc()
    p = dc.store.get(name)
    if p is None:
        raise HTTPException(404, f"profile '{name}' not found")

    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(400, "no fields to update")
    for k, v in changes.items():
        setattr(p, k, v)
    dc.store.update(p)
    return {"ok": True, "name": name, "updated": list(changes.keys())}


@app.post("/api/profiles/{name}/launch")
async def launch_profile(name: str, opts: LaunchOptions):
    dc = _dc()
    try:
        wrapper_pid = dc._launch_subprocess(
            name,
            headless=opts.headless,
            url=opts.url,
            force=opts.force,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ProfileAlreadyRunning as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "wrapper_pid": wrapper_pid}


@app.post("/api/profiles/{name}/stop")
async def stop_profile(name: str):
    dc = _dc()
    if not dc.store.exists(name):
        raise HTTPException(404, f"profile '{name}' not found")
    killed = dc.stop(name)
    return {"ok": True, "killed": killed}


@app.post("/api/profiles/launch-group")
async def launch_group(body: GroupLaunch):
    dc = _dc()
    pids = dc.launch_group(
        body.group, headless=body.headless, url=body.url, force=body.force
    )
    return {"ok": True, "wrapper_pids": pids}


@app.post("/api/profiles/stop-all")
async def stop_all():
    dc = _dc()
    killed = dc.stop_all()
    return {"ok": True, "killed": killed}


@app.get("/api/status/{name}")
async def status(name: str):
    dc = _dc()
    try:
        return dc.status(name)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/api/info")
async def info():
    dc = _dc()
    return {
        "root": str(dc.root),
        "version": "0.1.0",
        "playwright_version": _try_version("playwright"),
    }


def _try_version(pkg: str) -> Optional[str]:
    try:
        mod = __import__(pkg)
        return getattr(mod, "__version__", "unknown")
    except Exception:
        return None