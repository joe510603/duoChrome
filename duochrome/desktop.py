"""Desktop GUI wrapper around the Web UI.

Launches a native window (via pywebview, which uses the host's system WebView
— WKWebView on macOS, WebView2 on Windows, GTK WebKit on Linux) that loads
the FastAPI server in a background thread.

Usage:
    from duochrome.desktop import launch_desktop
    launch_desktop(port=0)  # 0 = pick a free port
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Optional

import uvicorn

from .core import DuoChrome


def _find_free_port() -> int:
    """Ask the OS for an unused TCP port. Avoids hard-coded clashes."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServerThread(threading.Thread):
    """Runs uvicorn in a background thread, exposes its bound port.

    We subclass Thread rather than use threading.Thread(target=...) so we
    can hold a reference to the uvicorn Server (needed to call .should_exit
    on window close).
    """

    def __init__(self, host: str, port: int):
        super().__init__(daemon=True, name="duochrome-web-server")
        from .web import app  # import here so DUOCHROME_ROOT env is honored

        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="warning",
                access_log=False,
            )
        )
        # port was already validated as free by _find_free_port() — we know
        # what address the server will bind to, no need to sniff it back.
        self.port = port
        self.bound_event = threading.Event()

    def run(self) -> None:
        # Watch for the public `started` flag (set after startup completes).
        # uvicorn 0.30+ exposes this; the older `servers` attribute was removed.
        def _wait_started():
            for _ in range(50):  # 5s budget, 100ms each
                if self._server.started:
                    self.bound_event.set()
                    return
                time.sleep(0.1)

        threading.Thread(target=_wait_started, daemon=True).start()
        self._server.run()

    def stop(self) -> None:
        self._server.should_exit = True


def launch_desktop(width: int = 1100, height: int = 720) -> None:
    """Open the duoChrome desktop GUI. Blocks until the window is closed.

    Spins up the FastAPI server in a background thread on a free port,
    then opens a native window pointing at it.
    """
    import webview

    port = _find_free_port()
    server_thread = _ServerThread(host="127.0.0.1", port=port)
    server_thread.start()

    # Wait for uvicorn to actually bind (max 5s)
    if not server_thread.bound_event.wait(timeout=5.0):
        server_thread.stop()
        raise RuntimeError("duoChrome web server failed to start within 5s")
    url = f"http://127.0.0.1:{port}"

    print(f"✓ duoChrome root: {DuoChrome().root}")
    print(f"✓ Desktop UI: {url}")
    print("  close the window to quit\n")

    window = webview.create_window(
        title="duoChrome",
        url=url,
        width=width,
        height=height,
        resizable=True,
        # macOS-only niceties — silently ignored on other platforms
        text_select=True,
    )

    try:
        webview.start()
    finally:
        server_thread.stop()
        server_thread.join(timeout=2.0)