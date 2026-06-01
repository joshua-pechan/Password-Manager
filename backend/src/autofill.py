"""Global hotkey autofill for desktop applications.

Registers a configurable hotkey (default Ctrl+Shift+F) via RegisterHotKey
(no system-wide keyboard hook).
On trigger:
  1. Captures the title of the currently focused window.
  2. Looks up matching 'app' credentials from the vault.
  3. If exactly one match: types username → Tab → password immediately.
  4. If multiple matches: calls show_picker(creds, hwnd) on the main thread
     so the GUI can present a selection dialog.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
import time
from typing import Callable


# ── Windows API constants ─────────────────────────────────────────────────────

MOD_ALT           = 0x0001
MOD_CONTROL       = 0x0002
MOD_SHIFT         = 0x0004
VK_F              = 0x46
VK_TAB            = 0x09
WM_HOTKEY         = 0x0312
WM_QUIT           = 0x0012
INPUT_KEYBOARD    = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP   = 0x0002
HOTKEY_ID         = 1

_KEY_TO_VK: dict[str, int] = {
    **{c.lower(): ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


# ── SendInput structures ──────────────────────────────────────────────────────

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         ctypes.wintypes.WORD),
        ("wScan",       ctypes.wintypes.WORD),
        ("dwFlags",     ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.wintypes.LONG),
        ("dy",          ctypes.wintypes.LONG),
        ("mouseData",   ctypes.wintypes.DWORD),
        ("dwFlags",     ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg",    ctypes.wintypes.DWORD),
        ("wParamL", ctypes.wintypes.WORD),
        ("wParamH", ctypes.wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type",  ctypes.wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


# ── Windows API helpers ───────────────────────────────────────────────────────

def get_foreground_info() -> tuple[int, str]:
    """Return (hwnd, window_title) of the currently active window."""
    user32 = ctypes.windll.user32
    hwnd   = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buf    = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return hwnd, buf.value


def focus_hwnd(hwnd: int) -> None:
    """Attempt to bring a window back to the foreground."""
    user32   = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg_tid   = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    self_tid = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(fg_tid, self_tid, True)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(fg_tid, self_tid, False)


# ── Credential injection (SendInput — no global hook) ─────────────────────────

def _make_unicode_event(char: str, key_up: bool = False) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
    ki = KEYBDINPUT(wVk=0, wScan=ord(char), dwFlags=flags, time=0, dwExtraInfo=0)
    return INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=ki))


def _make_vk_event(vk: int, key_up: bool = False) -> INPUT:
    flags = KEYEVENTF_KEYUP if key_up else 0
    ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    return INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=ki))


def _write(text: str, delay: float = 0.05) -> None:
    user32 = ctypes.windll.user32
    for char in text:
        events = (_make_unicode_event(char), _make_unicode_event(char, key_up=True))
        arr = (INPUT * 2)(*events)
        user32.SendInput(2, arr, ctypes.sizeof(INPUT))
        time.sleep(delay)


def _press_tab() -> None:
    events = (_make_vk_event(VK_TAB), _make_vk_event(VK_TAB, key_up=True))
    arr = (INPUT * 2)(*events)
    ctypes.windll.user32.SendInput(2, arr, ctypes.sizeof(INPUT))


def type_credentials(username: str, password: str) -> None:
    """Simulate typing username, Tab, then password into the focused window."""
    time.sleep(0.3)
    _write(username, delay=0.05)
    _press_tab()
    time.sleep(0.4)   # CEF/Electron apps (Steam, Discord) need extra time to move focus
    _write(password, delay=0.05)


# ── Hotkey configuration helpers ──────────────────────────────────────────────

def modifiers_vk_from_config(modifiers: list[str], key: str) -> tuple[int, int]:
    """Convert config strings (e.g. ['ctrl','shift'], 'f') to (mod_flags, vk_code)."""
    mod = 0
    for m in modifiers:
        ml = m.lower()
        if ml == "ctrl":    mod |= MOD_CONTROL
        elif ml == "shift": mod |= MOD_SHIFT
        elif ml == "alt":   mod |= MOD_ALT
    vk = _KEY_TO_VK.get(key.lower(), VK_F)
    return mod, vk


def hotkey_label(modifiers: int, vk: int) -> str:
    """Return a human-readable hotkey string, e.g. 'Ctrl+Shift+F'."""
    parts: list[str] = []
    if modifiers & MOD_CONTROL: parts.append("Ctrl")
    if modifiers & MOD_SHIFT:   parts.append("Shift")
    if modifiers & MOD_ALT:     parts.append("Alt")
    if 0x41 <= vk <= 0x5A:
        parts.append(chr(vk))
    elif 0x70 <= vk <= 0x7B:
        parts.append(f"F{vk - 0x6F}")
    else:
        parts.append(f"0x{vk:02X}")
    return "+".join(parts)


# ── Hotkey registration (RegisterHotKey — no global hook) ─────────────────────

_hotkey_thread_id: int | None = None
_hotkey_thread: threading.Thread | None = None


def _message_loop(on_hotkey: Callable, modifiers: int, vk: int) -> None:
    global _hotkey_thread_id
    user32   = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    _hotkey_thread_id = kernel32.GetCurrentThreadId()

    if not user32.RegisterHotKey(None, HOTKEY_ID, modifiers, vk):
        _hotkey_thread_id = None
        return

    msg = ctypes.wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                threading.Thread(target=on_hotkey, daemon=True).start()
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)
        _hotkey_thread_id = None


def stop() -> None:
    """Signal the hotkey message loop to exit."""
    tid = _hotkey_thread_id
    if tid is not None:
        ctypes.windll.user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)


def wait_stopped(timeout: float = 1.0) -> None:
    """Block until the hotkey thread has fully stopped and unregistered the key."""
    t = _hotkey_thread
    if t is not None and t.is_alive():
        t.join(timeout=timeout)


# ── Public API ────────────────────────────────────────────────────────────────

def start(
    vault_getter: Callable,
    show_picker: Callable,
    modifiers: int = MOD_CONTROL | MOD_SHIFT,
    vk: int = VK_F,
    schedule_fn: Callable | None = None,
    tk_root=None,
) -> bool:
    """
    Register a global hotkey via RegisterHotKey.

    vault_getter()            — returns the live Vault instance
    show_picker(creds, hwnd)  — called when multiple matches exist
    modifiers                 — OR'd MOD_* flags (default: Ctrl+Shift)
    vk                        — virtual key code (default: F)
    schedule_fn(fn)           — schedules fn() on the main thread
    tk_root                   — legacy alias for schedule_fn (tkinter root)

    Returns True on success, False if RegisterHotKey fails.
    """
    global _hotkey_thread

    if schedule_fn is None and tk_root is not None:
        schedule_fn = lambda fn: tk_root.after(0, fn)
    elif schedule_fn is None:
        schedule_fn = lambda fn: fn()

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
            schedule_fn(lambda: show_picker(matches, hwnd))

    t = threading.Thread(
        target=_message_loop,
        args=(_on_hotkey, modifiers, vk),
        daemon=True,
        name="hotkey-loop",
    )
    _hotkey_thread = t
    t.start()
    time.sleep(0.05)  # give the thread time to call RegisterHotKey
    return _hotkey_thread_id is not None
