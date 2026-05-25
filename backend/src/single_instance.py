"""Single-instance guard with bring-to-front IPC.

Usage (first instance):
    if not acquire("gui"):
        send_show_signal()   # tell existing instance to surface
        sys.exit(0)
    start_ipc_listener(lambda: app.after(0, app._do_restore))

The running instance listens on a localhost socket whose port is written to
~/.password_manager/gui.ipc so second instances can find it.
"""
from __future__ import annotations

import ctypes
import socket
import threading
from pathlib import Path

_IPC_FILE = Path.home() / ".password_manager" / "gui.ipc"
_handles: dict[str, int] = {}


def acquire(name: str) -> bool:
    """Return True if this is the first instance, False if another holds the mutex."""
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\PasswordManager_{name}")
    err    = ctypes.windll.kernel32.GetLastError()
    if err == 183:  # ERROR_ALREADY_EXISTS
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        return False
    if handle:
        _handles[name] = handle
    return True


def start_ipc_listener(on_show) -> None:
    """Bind a local socket, save its port, and call on_show() when signalled."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))   # OS picks any free port
    port = srv.getsockname()[1]
    srv.listen(5)

    _IPC_FILE.parent.mkdir(exist_ok=True, parents=True)
    _IPC_FILE.write_text(str(port))

    def _loop():
        while True:
            try:
                conn, _ = srv.accept()
                with conn:
                    if conn.recv(16).strip() == b"show":
                        on_show()
            except Exception:
                break

    threading.Thread(target=_loop, daemon=True, name="ipc-listen").start()


def send_show_signal() -> None:
    """Ask the already-running instance to bring itself to the front."""
    try:
        port = int(_IPC_FILE.read_text().strip())
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.5)
            s.connect(("127.0.0.1", port))
            s.sendall(b"show")
    except Exception:
        pass  # stale file or instance gone — nothing to do
