"""Global hotkey autofill for desktop applications.

Registers Ctrl+Shift+F system-wide. On trigger:
  1. Captures the title of the currently focused window.
  2. Looks up matching 'app' credentials from the vault.
  3. If exactly one match: types username → Tab → password immediately.
  4. If multiple matches: calls show_picker(creds, hwnd) on the main thread
     so the GUI can present a selection dialog.

Dependencies: keyboard (pip install keyboard)
"""
from __future__ import annotations

import ctypes
import threading
import time
from typing import Callable


# ── Windows API helpers ───────────────────────────────────────────────────────

def get_foreground_info() -> tuple[int, str]:
    """Return (hwnd, window_title) of the currently active window."""
    hwnd   = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf    = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return hwnd, buf.value


def focus_hwnd(hwnd: int) -> None:
    """Attempt to bring a window back to the foreground."""
    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # Attach thread input so SetForegroundWindow is allowed
    fg_tid   = ctypes.windll.user32.GetWindowThreadProcessId(
        ctypes.windll.user32.GetForegroundWindow(), None
    )
    self_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    ctypes.windll.user32.AttachThreadInput(fg_tid, self_tid, True)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    ctypes.windll.user32.AttachThreadInput(fg_tid, self_tid, False)


# ── Credential injection ──────────────────────────────────────────────────────

def type_credentials(username: str, password: str) -> None:
    """Simulate typing username, Tab, then password into the focused window."""
    import keyboard
    time.sleep(0.3)
    keyboard.write(username, delay=0.05)
    keyboard.press_and_release("tab")
    time.sleep(0.4)   # CEF/Electron apps (Steam, Discord) need extra time to move focus
    keyboard.write(password, delay=0.05)


# ── Hotkey registration ───────────────────────────────────────────────────────

def start(
    vault_getter: Callable,
    tk_root,
    show_picker: Callable,
) -> bool:
    """
    Register the global Ctrl+Shift+F hotkey.

    vault_getter() — returns the live Vault instance
    tk_root       — the tkinter root window (for scheduling on main thread)
    show_picker(creds, hwnd) — called when multiple matches exist

    Returns True if registration succeeded, False if `keyboard` is unavailable.
    """
    try:
        import keyboard as _kb
    except ImportError:
        return False

    def _on_hotkey() -> None:
        hwnd, title = get_foreground_info()
        if not title:
            return
        try:
            matches = vault_getter().get_by_app(title)
        except Exception:
            return
        if not matches:
            return
        if len(matches) == 1:
            cred = matches[0]
            threading.Thread(
                target=type_credentials,
                args=(cred["username"], cred["password"]),
                daemon=True,
            ).start()
        else:
            tk_root.after(0, lambda: show_picker(matches, hwnd))

    _kb.add_hotkey("ctrl+shift+f", _on_hotkey, suppress=True)
    return True
