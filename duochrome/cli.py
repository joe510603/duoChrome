"""duochrome CLI — thin wrapper over DuoChrome."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .core import DuoChrome, ProfileAlreadyRunning

app = typer.Typer(
    name="duochrome",
    help="Lightweight multi-profile browser isolation. Open N independent Chromiums.",
    no_args_is_help=True,
    add_completion=False,
)


# ---------- global --root ----------

_ROOT_KEY = "root"


@app.callback()
def _main(
    ctx: typer.Context,
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        "-r",
        help="DuoChrome data root (default: ~/.duochrome). Pass BEFORE the subcommand.",
        envvar="DUOCHROME_ROOT",
    ),
):
    """Persist --root into the context so every subcommand can read it."""
    ctx.ensure_object(dict)
    ctx.obj[_ROOT_KEY] = root


def _get_dc(ctx: typer.Context) -> DuoChrome:
    root = ctx.obj.get(_ROOT_KEY) if ctx.obj else None
    return DuoChrome(root=root) if root else DuoChrome()


def _fmt_time(ts: Optional[float]) -> str:
    if ts is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


# ---------- init ----------

@app.command()
def init(ctx: typer.Context):
    """Initialize the duoChrome data directory."""
    dc = _get_dc(ctx)
    dc.init()
    typer.echo(f"✓ initialized at {dc.root}")


# ---------- create ----------

@app.command()
def create(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Profile name (e.g. '门店A-账号1')"),
    group: str = typer.Option("default", "--group", "-g", help="Group label"),
    proxy: Optional[str] = typer.Option(None, "--proxy", "-p", help="Proxy URL: http://... or socks5://..."),
    ua: Optional[str] = typer.Option(None, "--ua", help="Custom User-Agent"),
    viewport: str = typer.Option("1280x800", "--viewport", help="WIDTHxHEIGHT"),
    notes: str = typer.Option("", "--notes", "-n", help="Free-form notes"),
):
    """Create a new profile."""
    dc = _get_dc(ctx)
    dc.init()
    try:
        w, h = (int(x) for x in viewport.lower().split("x"))
    except ValueError:
        typer.echo("✗ viewport must be WIDTHxHEIGHT (e.g. 1280x800)", err=True)
        raise typer.Exit(2)
    try:
        dc.create(name, group=group, proxy=proxy, user_agent=ua, viewport=(w, h), notes=notes)
    except FileExistsError as e:
        typer.echo(f"✗ {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"✓ created profile '{name}' (group={group})")


# ---------- ls ----------

@app.command("ls")
def list_cmd(
    ctx: typer.Context,
    group: Optional[str] = typer.Option(None, "--group", "-g"),
):
    """List all profiles (with alive status)."""
    dc = _get_dc(ctx)
    dc.init()
    items = dc.list(group=group)
    if not items:
        typer.echo("(no profiles yet — `duochrome create <name>` to add one)")
        return

    rows = []
    for p in items:
        rows.append(
            (
                p.name,
                p.group,
                p.proxy or "-",
                "●" if dc.store.is_alive(p.name) else "○",
                _fmt_time(p.last_opened_at),
            )
        )
    widths = [
        max(len(str(r[i])) for r in rows + [("NAME", "GROUP", "PROXY", "ALIVE", "LAST_OPENED")])
        for i in range(5)
    ]
    header = ("NAME", "GROUP", "PROXY", "ALIVE", "LAST_OPENED")
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    typer.echo(fmt.format(*header))
    typer.echo(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        typer.echo(fmt.format(*r))


# ---------- launch ----------

@app.command()
def launch(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(None, help="Profile name (omit if using --group)"),
    group: Optional[str] = typer.Option(None, "--group", "-g", help="Launch all profiles in this group"),
    headless: bool = typer.Option(False, "--headless", help="Run without UI"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Open this URL after launch"),
    force: bool = typer.Option(False, "--force", help="Launch even if profile is already running"),
):
    """Launch a Chrome window for one profile (or all in a group)."""
    if not name and not group:
        typer.echo("✗ need a profile NAME or --group", err=True)
        raise typer.Exit(2)
    if name and group:
        typer.echo("✗ give NAME or --group, not both", err=True)
        raise typer.Exit(2)

    dc = _get_dc(ctx)
    dc.init()

    if group:
        pids = dc.launch_group(group, headless=headless, url=url, force=force)
        if not pids:
            typer.echo(f"(no profiles in group '{group}')")
            return
        typer.echo(
            f"\n✓ {len(pids)} Chrome window(s) launched in group '{group}'."
            f"\n  Stop them with: duochrome stop --all --root {dc.root}"
            f"\n  Or kill the wrapper PIDs: {' '.join(str(p) for p in pids)}"
        )
        return

    try:
        ctx_obj = dc.launch(name, headless=headless, url=url, force=force)  # type: ignore[arg-type]
    except KeyError as e:
        typer.echo(f"✗ {e}", err=True)
        raise typer.Exit(1)
    except ProfileAlreadyRunning as e:
        typer.echo(f"✗ {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"✓ launched '{name}' (PID={dc.store.read_pid(name)}) — Ctrl+C to quit")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        ctx_obj.close()


# ---------- stop ----------

@app.command()
def stop(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(None, help="Profile name (omit with --all)"),
    all: bool = typer.Option(False, "--all", help="Stop every running profile"),
):
    """Stop a running profile's Chromium."""
    if not name and not all:
        typer.echo("✗ need a NAME or --all", err=True)
        raise typer.Exit(2)
    dc = _get_dc(ctx)
    if all:
        killed = dc.stop_all()
        typer.echo(f"✓ stopped {killed} profile(s)")
        return
    if dc.stop(name):  # type: ignore[arg-type]
        typer.echo(f"✓ stopped '{name}'")
    else:
        typer.echo(f"(profile '{name}' was not running)")


# ---------- rm ----------

@app.command()
def rm(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete a profile AND its on-disk Chrome data."""
    dc = _get_dc(ctx)
    if not dc.store.exists(name):
        typer.echo(f"✗ profile '{name}' does not exist", err=True)
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Delete profile '{name}' and all its browser data?", abort=True)
    dc.remove(name)
    typer.echo(f"✓ removed '{name}'")


# ---------- status ----------

@app.command()
def status(
    ctx: typer.Context,
    name: str = typer.Argument(...),
):
    """Show detailed status for one profile."""
    dc = _get_dc(ctx)
    try:
        s = dc.status(name)
    except KeyError:
        typer.echo(f"✗ profile '{name}' not found", err=True)
        raise typer.Exit(1)
    for k, v in s.items():
        typer.echo(f"{k:<14}: {v}")


# ---------- version ----------

@app.command()
def version():
    """Print version."""
    typer.echo(f"duochrome {__version__}")


# ---------- ui ----------

@app.command()
def ui(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(9876, "--port", "-p", help="Bind port"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't auto-open browser"),
):
    """Start the web UI (FastAPI + uvicorn) and open it in your browser."""
    import threading
    import time
    import webbrowser

    import uvicorn

    dc = _get_dc(ctx)
    dc.init()
    typer.echo(f"✓ duoChrome root: {dc.root}")
    typer.echo(f"✓ Web UI: http://{host}:{port}")
    typer.echo("  Ctrl+C to stop\n")

    if not no_browser:
        def _open():
            time.sleep(1.0)  # let uvicorn bind first
            webbrowser.open(f"http://{host}:{port}")
        threading.Thread(target=_open, daemon=True).start()

    # Import after dc.init so DUOCHROME_ROOT env (if set) is honored.
    try:
        from duochrome.web import app as web_app
    except ImportError as e:
        typer.echo(f"✗ FastAPI/uvicorn not installed: {e}", err=True)
        typer.echo("  pip install fastapi uvicorn", err=True)
        raise typer.Exit(1)

    config = uvicorn.Config(web_app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    try:
        server.run()
    except KeyboardInterrupt:
        typer.echo("\n✓ stopped")


# ---------- desktop ----------

@app.command()
def desktop(
    ctx: typer.Context,
    width: int = typer.Option(1100, "--width", help="Window width"),
    height: int = typer.Option(720, "--height", help="Window height"),
):
    """Open the duoChrome desktop GUI (native window via pywebview).

    Wraps the same Web UI in a native WKWebView (macOS) / WebView2 (Windows) /
    GTK WebKit (Linux) window. Closes the window to quit.
    """
    try:
        import webview  # noqa: F401
    except ImportError:
        typer.echo("✗ pywebview not installed", err=True)
        typer.echo("  pip install pywebview", err=True)
        raise typer.Exit(1)

    dc = _get_dc(ctx)
    dc.init()

    from duochrome.desktop import launch_desktop
    launch_desktop(width=width, height=height)


if __name__ == "__main__":
    app()