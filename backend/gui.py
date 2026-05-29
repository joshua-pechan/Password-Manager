"""Desktop GUI for the password manager."""
from __future__ import annotations

import colorsys
import hashlib
import http.server
import io
import json
import logging
import os
import pathlib
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import winreg

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, filedialog
import csv

from vault import Vault, VaultError
from device_key import load as _load_key, path as _key_path, exists as _key_exists
from database import DB_PATH
import autofill as _autofill

logging.getLogger("werkzeug").setLevel(logging.ERROR)


def _apply_icon(window) -> None:
    """Set the lock icon on any CTk window or Toplevel."""
    try:
        import tempfile
        from PIL import Image
        if getattr(sys, "frozen", False):
            icon_src = pathlib.Path(sys._MEIPASS) / "icon128.png"
        else:
            icon_src = (
                pathlib.Path(__file__).parent.parent
                / "extension" / "icons" / "icon128.png"
            )
        ico_path = pathlib.Path(tempfile.gettempdir()) / "pm_icon.ico"
        if not ico_path.exists():
            img = Image.open(icon_src)
            img.save(str(ico_path), format="ICO", sizes=[(32, 32), (48, 48)])
        window.iconbitmap(str(ico_path))
    except Exception:
        pass


def _read_ext_path() -> str:
    """Return the Chrome extension folder recorded by the installer, or ''."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PasswordManager")
        val = winreg.QueryValueEx(key, "ExtensionPath")[0]
        winreg.CloseKey(key)
        return val
    except Exception:
        return ""

from single_instance import acquire as _acquire_lock, send_show_signal as _send_show, start_ipc_listener as _start_ipc
if not _acquire_lock("gui"):
    _send_show()
    sys.exit(0)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Design tokens ─────────────────────────────────────────────────────────────

BG         = "#111827"
HEADER_BG  = "#0a0f1a"
SURFACE    = "#1f2937"
SURFACE_HV = "#263445"
BORDER     = "#374151"
BORDER_HL  = "#3b82f6"
INPUT_BG   = "#1a2436"
ACCENT     = "#3b82f6"
ACCENT_HV  = "#2563eb"
DANGER     = "#ef4444"
DANGER_HV  = "#dc2626"
TEXT       = "#f9fafb"
TEXT2      = "#9ca3af"
GREEN      = "#4ade80"
SECTION_FG = "#4b5563"   # muted section header text

FONT_SM    = ("Segoe UI", 11)
FONT       = ("Segoe UI", 12)
FONT_BOLD  = ("Segoe UI", 13, "bold")
FONT_TITLE = ("Segoe UI", 17, "bold")
CLIP_TTL   = 30

# ── App config (start-in-tray, etc.) ─────────────────────────────────────────

_CONFIG_PATH = pathlib.Path.home() / ".password_manager" / "config.json"

def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_config(cfg: dict) -> None:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass

def _get_hotkey_label() -> str:
    cfg = _load_config()
    mods = cfg.get("hotkey_modifiers", ["ctrl", "shift"])
    key  = cfg.get("hotkey_key", "f")
    try:
        mod_flags, vk = _autofill.modifiers_vk_from_config(mods, key)
        return _autofill.hotkey_label(mod_flags, vk)
    except Exception:
        return "Ctrl+Shift+F"

# ── Shared hover state (one highlight across all cards AND groups) ─────────────
_hover_state: dict = {"item": None}

# ── Password generator helpers ────────────────────────────────────────────────

_GEN_UPPER   = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
_GEN_LOWER   = 'abcdefghijklmnopqrstuvwxyz'
_GEN_DIGITS  = '0123456789'
_GEN_SYMBOLS = '!@#$%^&*()-_=+[]{}|;:,.<>?'

def _gen_password(length: int, upper: bool, lower: bool, digits: bool, symbols: bool) -> str:
    pool, required = '', []
    if upper:   pool += _GEN_UPPER;   required.append(secrets.choice(_GEN_UPPER))
    if lower:   pool += _GEN_LOWER;   required.append(secrets.choice(_GEN_LOWER))
    if digits:  pool += _GEN_DIGITS;  required.append(secrets.choice(_GEN_DIGITS))
    if symbols: pool += _GEN_SYMBOLS; required.append(secrets.choice(_GEN_SYMBOLS))
    if not pool:
        return ''
    chars = [secrets.choice(pool) for _ in range(length)]
    for i, ch in enumerate(required):
        chars[i] = ch
    for i in range(len(chars) - 1, 0, -1):          # Fisher-Yates shuffle
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return ''.join(chars)

def _password_strength(pwd: str) -> tuple[int, str, str]:
    """Return (0-100 pct, label, hex color) matching the Chrome extension logic."""
    if not pwd:
        return 0, '', '#555555'
    score = 0
    if len(pwd) >= 12: score += 1
    if len(pwd) >= 16: score += 1
    if len(pwd) >= 20: score += 1
    if re.search(r'[A-Z]',       pwd): score += 1
    if re.search(r'[a-z]',       pwd): score += 1
    if re.search(r'[0-9]',       pwd): score += 1
    if re.search(r'[^A-Za-z0-9]', pwd): score += 1
    if score <= 2: return 20,  'Weak',        '#ef4444'
    if score <= 4: return 50,  'Fair',        '#f97316'
    if score <= 5: return 75,  'Strong',      '#eab308'
    return              100, 'Very Strong', '#22c55e'


def _make_tray_icon():
    from PIL import Image, ImageDraw
    sz = 64
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    d.ellipse([1, 1, sz - 2, sz - 2], fill=(31, 83, 141, 255))
    bw, bh = int(sz * 0.44), int(sz * 0.32)
    bx = (sz - bw) // 2
    by = int(sz * 0.50)
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=3, fill=(255, 255, 255, 255))
    sw = int(sz * 0.28)
    sx = (sz - sw) // 2
    sy = int(sz * 0.20)
    d.arc([sx, sy, sx + sw, sy + int(sz * 0.38)], start=0, end=180,
          fill=(255, 255, 255, 255), width=max(3, sz // 11))
    return img


_AVATAR_PALETTE = [
    "#6366f1",  # indigo
    "#8b5cf6",  # violet
    "#ec4899",  # pink
    "#f97316",  # orange
    "#eab308",  # yellow
    "#22c55e",  # green
    "#14b8a6",  # teal
    "#0ea5e9",  # sky
]

def _darken_hex(color: str, factor: float) -> str:
    """Same hue and saturation, value multiplied by factor (HSV)."""
    h = color.lstrip('#')
    r, g, b = (int(h[i:i+2], 16) / 255 for i in (0, 2, 4))
    hue, sat, val = colorsys.rgb_to_hsv(r, g, b)
    r2, g2, b2 = colorsys.hsv_to_rgb(hue, sat, val * factor)
    return '#{:02x}{:02x}{:02x}'.format(round(r2 * 255), round(g2 * 255), round(b2 * 255))

def _avatar_color(name: str, variant: int = 0) -> str:
    idx  = ord(name[0].upper()) % len(_AVATAR_PALETTE) if name else 0
    base = _AVATAR_PALETTE[idx]
    return base if variant == 0 else _darken_hex(base, 0.8)

def _initial(name: str) -> str:
    return (name[0] if name else "?").upper()


def _get_local_ip() -> str:
    """Return the machine's primary LAN IP (works on Ethernet and WiFi)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ── One-time LAN export server ────────────────────────────────────────────────

class _PhoneExportServer:
    """Serves a single password CSV over LAN using a one-time token, then shuts down."""

    def __init__(self, vault, token: str, port: int) -> None:
        self.used    = threading.Event()
        self._vault  = vault
        self._token  = token
        self._port   = port
        self._server: http.server.HTTPServer | None = None
        threading.Thread(target=self._run, daemon=True, name="phone-export").start()

    def _run(self) -> None:
        vault  = self._vault
        token  = self._token
        used   = self.used

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != f"/export/{token}":
                    self.send_response(404)
                    self.end_headers()
                    return
                creds = vault.get_all()
                buf   = io.StringIO()
                w     = csv.writer(buf)
                w.writerow(["Title", "URL", "Username", "Password", "Notes", "Type"])
                for c in creds:
                    title = c.get("app_name") or c["domain"]
                    w.writerow([title, c["url"], c["username"],
                                c["password"], "", c.get("cred_type", "web")])
                data = buf.getvalue().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type",        "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="passwords.csv"')
                self.send_header("Content-Length",      str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
                used.set()

            def log_message(self, *_):
                pass

        try:
            srv          = http.server.HTTPServer(("0.0.0.0", self._port), Handler)
            srv.timeout  = 1
            self._server = srv
            deadline     = time.monotonic() + 65
            while not used.is_set() and time.monotonic() < deadline:
                srv.handle_request()
        finally:
            if self._server:
                self._server.server_close()

    def shutdown(self) -> None:
        self.used.set()
        try:
            if self._server:
                self._server.server_close()
        except Exception:
            pass


# ── App window ────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.configure(fg_color=BG)
        self.vault = Vault()
        self.title("Password Manager")
        self.after(200, self._set_window_icon)
        self.geometry("920x660")
        self.minsize(680, 460)
        self._frame: ctk.CTkFrame | None = None
        self._tray  = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        _start_ipc(lambda: self.after(0, self._do_restore))
        self._auto_unlock()

    def _auto_unlock(self) -> None:
        if not _key_exists() and self.vault.is_initialized():
            messagebox.showerror(
                "Vault Inaccessible",
                f"Device key missing:\n{_key_path()}\n\n"
                f"Delete the database to start fresh:\n{DB_PATH}",
            )
            self.destroy()
            return
        try:
            key = _load_key()
            if not self.vault.is_initialized():
                self.vault.setup(key)
            else:
                self.vault.unlock(key)
        except Exception as exc:
            messagebox.showerror("Startup Error", str(exc))
            self.destroy()
            return
        self._start_server()
        self._go(MainFrame)
        self._start_autofill()
        if _load_config().get("start_in_tray"):
            self.after(300, self._on_close)

    def _start_autofill(self) -> None:
        def _picker(creds, hwnd):
            AppFillDialog(self, creds, hwnd)
        cfg = _load_config()
        mods_cfg = cfg.get("hotkey_modifiers", ["ctrl", "shift"])
        key_cfg  = cfg.get("hotkey_key", "f")
        try:
            modifiers, vk = _autofill.modifiers_vk_from_config(mods_cfg, key_cfg)
        except Exception:
            modifiers, vk = _autofill.MOD_CONTROL | _autofill.MOD_SHIFT, _autofill.VK_F
        _autofill.start(
            vault_getter=lambda: self.vault,
            tk_root=self,
            show_picker=_picker,
            modifiers=modifiers,
            vk=vk,
        )

    def _open_hotkey_dialog(self) -> None:
        cfg = _load_config()
        mods_cfg = cfg.get("hotkey_modifiers", ["ctrl", "shift"])
        key_cfg  = cfg.get("hotkey_key", "f")
        try:
            mod_flags, vk = _autofill.modifiers_vk_from_config(mods_cfg, key_cfg)
        except Exception:
            mod_flags, vk = _autofill.MOD_CONTROL | _autofill.MOD_SHIFT, _autofill.VK_F
        current_label = _autofill.hotkey_label(mod_flags, vk)

        _autofill.stop()  # release hotkey so user can press the current combo in the dialog

        def _on_save(new_mod_flags: int, new_vk: int, _label: str) -> None:
            mod_names: list[str] = []
            if new_mod_flags & _autofill.MOD_CONTROL: mod_names.append("ctrl")
            if new_mod_flags & _autofill.MOD_SHIFT:   mod_names.append("shift")
            if new_mod_flags & _autofill.MOD_ALT:     mod_names.append("alt")
            key_name = (
                chr(new_vk).lower() if 0x41 <= new_vk <= 0x5A
                else f"f{new_vk - 0x6F}" if 0x70 <= new_vk <= 0x7B
                else "f"
            )
            cfg2 = _load_config()
            cfg2["hotkey_modifiers"] = mod_names
            cfg2["hotkey_key"] = key_name
            _save_config(cfg2)

        def _on_dialog_close() -> None:
            _autofill.wait_stopped()
            cfg3 = _load_config()
            mods3 = cfg3.get("hotkey_modifiers", mods_cfg)
            key3  = cfg3.get("hotkey_key", key_cfg)
            try:
                mf3, v3 = _autofill.modifiers_vk_from_config(mods3, key3)
            except Exception:
                mf3, v3 = _autofill.MOD_CONTROL | _autofill.MOD_SHIFT, _autofill.VK_F
            _autofill.start(
                vault_getter=lambda: self.vault,
                tk_root=self,
                show_picker=lambda creds, hwnd: AppFillDialog(self, creds, hwnd),
                modifiers=mf3,
                vk=v3,
            )

        HotkeyDialog(self, current_label, _on_save, on_close=_on_dialog_close)

    def _go(self, cls, **kw) -> None:
        if self._frame:
            self._frame.destroy()
        self._frame = cls(self, **kw)
        self._frame.pack(fill="both", expand=True)

    def _start_server(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", 7412)) == 0:
                return
        import server as srv
        threading.Thread(target=srv.run, daemon=True, name="api").start()

    def _on_close(self) -> None:
        self.withdraw()
        if self._tray is not None:
            return
        try:
            import pystray
            menu = pystray.Menu(
                pystray.MenuItem("Open Password Manager", self._restore_window, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._quit_from_tray),
            )
            self._tray = pystray.Icon("pwmgr", _make_tray_icon(), "Password Manager", menu)
            threading.Thread(target=self._tray.run, daemon=True, name="tray").start()
        except Exception:
            self.destroy()

    def _restore_window(self, icon=None, item=None) -> None:
        self.after(0, self._do_restore)

    def _do_restore(self) -> None:
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_from_tray(self, icon=None, item=None) -> None:
        if self._tray:
            self._tray.stop()
            self._tray = None
        _autofill.stop()
        self.after(0, self.destroy)

    def _set_window_icon(self) -> None:
        _apply_icon(self)


# ── Main view ─────────────────────────────────────────────────────────────────

class MainFrame(ctk.CTkFrame):
    def __init__(self, parent: App, **kw) -> None:
        super().__init__(parent, fg_color=BG, **kw)
        self._app = parent
        self._clip_timer: threading.Timer | None = None
        self._db_hash: str = self._get_db_hash()
        self._collapsed: dict[str, bool] = {}   # group_key → collapsed state
        self._group_keys: list[str] = []         # keys of current multi-cred groups
        self._active_tab: str = "web"
        self._tab_btns: dict[str, tuple] = {}
        self._tab_order: list[str] = []
        self._custom_tabs: list[str] = []
        self._tab_w: int = 140
        # tab drag-to-reorder state
        self._drag_tab_key: str | None = None
        self._drag_tab_ghost: tk.Frame | None = None
        self._drag_tab_press_xy: tuple | None = None
        self._drag_tab_active: bool = False
        self._drop_zones: list  = []             # (group_key, header_widget) for drag-to-group
        self._drag_label: tk.Frame | None = None
        self._drag_cred: dict | None = None
        self._drag_press_xy: tuple | None = None
        self._drag_active: bool = False

        self._build_header()
        self._build_search()
        self._build_tabs()
        self._build_list()
        self._refresh()

        parent.bind("<ButtonPress-1>",   self._on_global_press,   add="+")
        parent.bind("<B1-Motion>",        self._on_global_motion,  add="+")
        parent.bind("<ButtonRelease-1>",  self._on_global_release, add="+")

        parent.bind_all("<Control-n>", lambda _: self._open_add())
        parent.bind_all("<Control-f>", lambda _: self._search_entry.focus_set())
        parent.bind_all("<Escape>",    lambda _: self._search_var.set(""))

        self.after(2000, self._poll_db)

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, height=62, fg_color=HEADER_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        ctk.CTkLabel(
            hdr, text="Password Manager",
            font=FONT_TITLE, text_color=TEXT,
        ).pack(side="left", padx=20)

        ctk.CTkButton(
            hdr, text="Info", width=68, height=34, font=FONT_SM,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE, text_color=TEXT2,
            command=self._show_info,
        ).pack(side="right", padx=(4, 16), pady=14)

        self._transfer_btn = ctk.CTkButton(
            hdr, text="Transfer ▾", width=106, height=34, font=FONT_SM,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE, text_color=TEXT2,
            command=self._show_transfer_menu,
        )
        self._transfer_btn.pack(side="right", padx=(4, 0), pady=14)

        ctk.CTkButton(
            hdr, text="Generator", width=100, height=34, font=FONT_SM,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE, text_color=TEXT2,
            command=self._open_generator,
        ).pack(side="right", padx=4, pady=14)

        ctk.CTkButton(
            hdr, text="+ Group", width=90, height=34, font=FONT,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE, text_color=TEXT2, corner_radius=8,
            command=self._open_new_group,
        ).pack(side="right", padx=4, pady=14)

        ctk.CTkButton(
            hdr, text="+ Add", width=90, height=34, font=FONT_BOLD,
            fg_color=ACCENT, hover_color=ACCENT_HV, corner_radius=8,
            command=self._open_add,
        ).pack(side="right", padx=(0, 4), pady=14)

        self._count_lbl = ctk.CTkLabel(hdr, text="", font=FONT_SM, text_color=TEXT2)
        self._count_lbl.pack(side="right", padx=12)

    # ── Search bar ────────────────────────────────────────────────────────────

    def _build_search(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=HEADER_BG, corner_radius=0)
        bar.pack(fill="x")
        ctk.CTkFrame(bar, height=1, fg_color=BORDER, corner_radius=0).pack(fill="x")

        search_row = ctk.CTkFrame(bar, fg_color="transparent")
        search_row.pack(fill="x", padx=16, pady=10)

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._refresh())

        self._search_entry = ctk.CTkEntry(
            search_row,
            textvariable=self._search_var,
            placeholder_text="🔍   Search by site, app, username, or URL…",
            height=38, font=FONT,
            fg_color=INPUT_BG, border_color=BORDER,
            border_width=1, corner_radius=8, text_color=TEXT,
        )
        self._search_entry.pack(side="left", fill="x", expand=True)

        self._collapse_btn = ctk.CTkButton(
            search_row, text="Expand All", width=106, height=38, font=FONT_SM,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE, text_color=SECTION_FG,
            command=self._toggle_all_groups,
        )
        self._collapse_btn.pack(side="left", padx=(8, 0))

    # ── Tab bar ───────────────────────────────────────────────────────────────

    # human-readable labels for built-in tabs
    _BUILTIN_LABELS = {"web": "Websites", "app": "Apps"}

    def _build_tabs(self) -> None:
        outer = ctk.CTkFrame(self, fg_color=HEADER_BG, corner_radius=0)
        outer.pack(fill="x")
        ctk.CTkFrame(outer, height=1, fg_color=BORDER, corner_radius=0).pack(fill="x")

        row = tk.Frame(outer, bg=HEADER_BG)
        row.pack(fill="x")

        # Scroll arrows — packed after canvas so they sit flush at each side
        self._tab_scroll_left = tk.Button(
            row, text="‹", bg=HEADER_BG, fg=TEXT2,
            font=("Segoe UI", 14, "bold"), relief="flat", bd=0,
            activebackground=SURFACE, activeforeground=TEXT,
            cursor="hand2", padx=4,
            command=lambda: self._tab_canvas.xview_scroll(-3, "units"),
        )
        self._tab_scroll_right = tk.Button(
            row, text="›", bg=HEADER_BG, fg=TEXT2,
            font=("Segoe UI", 14, "bold"), relief="flat", bd=0,
            activebackground=SURFACE, activeforeground=TEXT,
            cursor="hand2", padx=4,
            command=lambda: self._tab_canvas.xview_scroll(3, "units"),
        )

        # "+ Tab" button — always visible, fixed on right
        tk.Button(
            row, text="+",
            bg=HEADER_BG, fg=TEXT2,
            font=("Segoe UI", 15), relief="flat", bd=0,
            activebackground=SURFACE, activeforeground=TEXT,
            cursor="hand2", padx=8, pady=2,
            command=self._open_add_tab,
        ).pack(side="right", padx=(0, 6))

        self._tab_canvas = tk.Canvas(row, height=44, bg=HEADER_BG,
                                      highlightthickness=0, bd=0)
        self._tab_canvas.pack(side="left", fill="x", expand=True)

        self._tab_inner = tk.Frame(self._tab_canvas, bg=HEADER_BG)
        self._tab_canvas.create_window((0, 0), window=self._tab_inner, anchor="nw")

        self._tab_inner.bind("<Configure>",  self._on_tab_inner_configure)
        self._tab_canvas.bind("<Configure>", self._on_tab_canvas_configure)
        self._tab_canvas.bind("<MouseWheel>", self._on_tab_scroll)

        self._rebuild_tab_row()

    # ── Tab-bar rebuild ───────────────────────────────────────────────────────

    def _rebuild_tab_row(self) -> None:
        """Destroy and recreate all tab widgets in the canvas inner frame."""
        for w in self._tab_inner.winfo_children():
            w.destroy()
        self._tab_btns.clear()

        try:
            self._custom_tabs = self._app.vault.get_custom_tabs()
        except Exception:
            self._custom_tabs = []

        try:
            self._tab_order = self._app.vault.get_tab_order()
        except Exception:
            self._tab_order = ["web", "app"] + list(self._custom_tabs)

        self._tab_w = self._calc_tab_width()

        for key in self._tab_order:
            label = self._BUILTIN_LABELS.get(key, key)
            is_active  = key == self._active_tab
            is_custom  = key not in self._BUILTIN_LABELS

            wrap = tk.Frame(self._tab_inner, bg=HEADER_BG,
                            width=self._tab_w, height=44)
            wrap.pack(side="left")
            wrap.pack_propagate(False)

            lbl = tk.Label(
                wrap, text=label,
                bg=HEADER_BG, fg=ACCENT if is_active else TEXT2,
                font=("Segoe UI", 11), cursor="hand2",
            )
            lbl.place(relx=0.5 if not is_custom else 0.45,
                      rely=0.42, anchor="center")

            underline = tk.Frame(wrap, height=2,
                                 bg=ACCENT if is_active else HEADER_BG)
            underline.place(relx=0, rely=1.0, anchor="sw", relwidth=1)

            if is_custom:
                x_lbl = tk.Label(
                    wrap, text="×",
                    bg=HEADER_BG, fg=BORDER,
                    font=("Segoe UI", 12), cursor="hand2",
                )
                x_lbl.place(relx=0.92, rely=0.38, anchor="center")
                x_lbl.bind("<ButtonPress-1>",
                            lambda e, k=key: self._remove_tab(k))
                x_lbl.bind("<Enter>",
                            lambda e, xl=x_lbl: xl.configure(fg=DANGER))
                x_lbl.bind("<Leave>",
                            lambda e, xl=x_lbl: xl.configure(fg=BORDER))

            self._tab_btns[key] = (lbl, underline)

            for widget in (wrap, lbl, underline):
                widget.bind("<MouseWheel>",     self._on_tab_scroll)
                widget.bind("<ButtonPress-1>",  lambda e, k=key: self._on_tab_press(e, k))
                widget.bind("<B1-Motion>",       self._on_tab_motion)
                widget.bind("<ButtonRelease-1>", self._on_tab_release)

        self._tab_inner.update_idletasks()
        self._tab_canvas.configure(
            scrollregion=self._tab_canvas.bbox("all") or (0, 0, 0, 0)
        )
        self._update_scroll_buttons()

    def _calc_tab_width(self) -> int:
        n = len(self._tab_order)
        if n == 0:
            return 140
        canvas_w = self._tab_canvas.winfo_width()
        if canvas_w <= 1:
            return 140
        ideal = canvas_w // n
        return max(80, min(180, ideal))

    def _on_tab_inner_configure(self, _event=None) -> None:
        self._tab_canvas.configure(
            scrollregion=self._tab_canvas.bbox("all") or (0, 0, 0, 0)
        )
        self._update_scroll_buttons()

    def _on_tab_canvas_configure(self, _event=None) -> None:
        new_w = self._calc_tab_width()
        if new_w != self._tab_w:
            self._tab_w = new_w
            self._rebuild_tab_row()
        else:
            self._update_scroll_buttons()

    def _update_scroll_buttons(self) -> None:
        try:
            inner_w = self._tab_inner.winfo_reqwidth()
            canvas_w = self._tab_canvas.winfo_width()
            needs_scroll = inner_w > canvas_w + 2
            if needs_scroll:
                self._tab_scroll_left.pack(side="left",  before=self._tab_canvas, padx=(4, 0))
                self._tab_scroll_right.pack(side="right", before=self._tab_canvas)
            else:
                self._tab_scroll_left.pack_forget()
                self._tab_scroll_right.pack_forget()
        except Exception:
            pass

    def _on_tab_scroll(self, event) -> None:
        self._tab_canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")

    # ── Tab drag-to-reorder ───────────────────────────────────────────────────

    def _on_tab_press(self, event, key: str) -> None:
        self._drag_tab_key      = key
        self._drag_tab_press_xy = (event.x_root, event.y_root)
        self._drag_tab_active   = False
        # Record where within the tab the click landed so the ghost follows naturally
        try:
            canvas_x = self._tab_canvas.canvasx(
                event.x_root - self._tab_canvas.winfo_rootx()
            )
            tab_idx = self._tab_order.index(key) if key in self._tab_order else 0
            self._drag_tab_offset = int(canvas_x) - tab_idx * self._tab_w
        except Exception:
            self._drag_tab_offset = self._tab_w // 2

    def _on_tab_motion(self, event) -> None:
        if not self._drag_tab_key or not self._drag_tab_press_xy:
            return
        if not self._drag_tab_active:
            if abs(event.x_root - self._drag_tab_press_xy[0]) < 6:
                return
            self._drag_tab_active = True
            self._drag_tab_start(event)
        if self._drag_tab_active:
            self._drag_tab_update(event)

    def _on_tab_release(self, event) -> None:
        if self._drag_tab_active and self._drag_tab_key:
            self._drag_tab_commit(event)
        elif self._drag_tab_key and not self._drag_tab_active:
            self._switch_tab(self._drag_tab_key)
        self._drag_tab_cleanup()

    def _drag_tab_start(self, event) -> None:
        if self._drag_tab_ghost and self._drag_tab_ghost.winfo_exists():
            self._drag_tab_ghost.destroy()
        key   = self._drag_tab_key
        label = self._BUILTIN_LABELS.get(key, key)
        ghost = tk.Frame(
            self._app, bg=ACCENT,
            highlightthickness=1, highlightbackground=ACCENT_HV,
        )
        tk.Label(ghost, text=f"  {label}  ",
                 bg=ACCENT, fg="#ffffff",
                 font=("Segoe UI", 11), pady=5).pack()
        ghost.place(x=-300, y=-300)
        ghost.lift()
        self._drag_tab_ghost = ghost

    def _drag_tab_update(self, event) -> None:
        if self._drag_tab_ghost and self._drag_tab_ghost.winfo_exists():
            offset = getattr(self, "_drag_tab_offset", self._tab_w // 2)
            x = event.x_root - self._app.winfo_rootx() - offset
            y = self._tab_canvas.winfo_rooty() - self._app.winfo_rooty()
            self._drag_tab_ghost.place(x=x, y=y)

    def _drag_tab_commit(self, event) -> None:
        raw_x    = event.x_root - self._tab_canvas.winfo_rootx()
        canvas_x = self._tab_canvas.canvasx(raw_x)
        tw       = max(1, self._tab_w)
        target   = max(0, min(int(canvas_x) // tw, len(self._tab_order) - 1))
        key      = self._drag_tab_key
        if key in self._tab_order:
            order = list(self._tab_order)
            order.pop(order.index(key))
            order.insert(target, key)
            self._tab_order = order
            try:
                self._app.vault.set_tab_order(order)
            except Exception:
                pass

    def _drag_tab_cleanup(self) -> None:
        if self._drag_tab_ghost and self._drag_tab_ghost.winfo_exists():
            self._drag_tab_ghost.destroy()
        self._drag_tab_ghost    = None
        self._drag_tab_key      = None
        self._drag_tab_press_xy = None
        was_active = self._drag_tab_active
        self._drag_tab_active   = False
        if was_active:
            self._rebuild_tab_row()

    # ── Tab switch ────────────────────────────────────────────────────────────

    def _switch_tab(self, tab: str) -> None:
        self._active_tab = tab
        for key, (lbl, underline) in self._tab_btns.items():
            active = key == tab
            lbl.configure(fg=ACCENT if active else TEXT2)
            underline.configure(bg=ACCENT if active else HEADER_BG)
        self._scroll.pack(fill="both", expand=True)
        self._refresh()

    # ── Scrollable list ───────────────────────────────────────────────────────

    def _build_list(self) -> None:
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color=BG,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=SURFACE_HV,
            corner_radius=0,
        )
        self._scroll.pack(fill="both", expand=True)

        self._empty = ctk.CTkFrame(self._scroll, fg_color="transparent")
        ctk.CTkLabel(
            self._empty, text="🔑",
            font=("Segoe UI Emoji", 48), text_color=BORDER,
        ).pack(pady=(60, 10))
        self._empty_title = ctk.CTkLabel(
            self._empty, text="No passwords saved yet",
            font=FONT_BOLD, text_color=TEXT2,
        )
        self._empty_title.pack()
        self._empty_sub = ctk.CTkLabel(
            self._empty, text='Click  "+ Add"  to save your first login',
            font=FONT_SM, text_color=BORDER,
        )
        self._empty_sub.pack(pady=(4, 0))

    # ── Data refresh ──────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        _hover_state["item"] = None          # clear any stale reference before rebuild
        self._group_keys = []
        self._drop_zones  = []
        query = self._search_var.get().strip().lower()
        try:
            all_creds = self._app.vault.get_all()
        except VaultError:
            return

        total = len(all_creds)
        if query:
            creds = [
                c for c in all_creds
                if query in c["domain"].lower()
                or query in c["username"].lower()
                or query in c.get("url", "").lower()
                or query in c.get("app_name", "").lower()
            ]
        else:
            creds = all_creds

        for w in self._scroll.winfo_children():
            if w is not self._empty:
                w.destroy()

        active = [c for c in creds if c.get("cred_type", "web") == self._active_tab]

        if not active:
            if query:
                self._empty_title.configure(text=f'No results for "{query}"')
                self._empty_sub.configure(text="Try a different search term")
            elif self._active_tab == "app":
                self._empty_title.configure(text="No app logins saved yet")
                self._empty_sub.configure(text='Click  "+ Add"  →  select  "App"  to add one')
            elif self._active_tab == "web":
                self._empty_title.configure(text="No website logins saved yet")
                self._empty_sub.configure(text='Click  "+ Add"  to save your first login')
            else:
                tab_label = self._active_tab
                self._empty_title.configure(text=f'No "{tab_label}" logins saved yet')
                self._empty_sub.configure(text='Click  "+ Add"  to add one')
            self._empty.pack(fill="x")
            self._count_lbl.configure(text=f"{total} saved" if total else "")
            self._update_collapse_btn()
            return

        self._empty.pack_forget()

        def _group(cred_list: list) -> dict[str, list]:
            groups: dict[str, list] = {}
            for c in cred_list:
                key = c.get("group_name") or c["domain"]
                groups.setdefault(key, []).append(c)
            return groups

        # Fetch standalone (possibly empty) groups created via + Group
        try:
            standalone: set[str] = set(self._app.vault.get_groups(self._active_tab))
        except Exception:
            standalone = set()

        # Pre-populate grouping dict with standalone groups so they appear even when empty
        grouped: dict[str, list] = {name: [] for name in standalone}
        for c in active:
            key = c.get("group_name") or c["domain"]
            grouped.setdefault(key, []).append(c)

        _color_counts: dict[int, int] = {}

        def _variant(name: str) -> int:
            idx = ord(name[0].upper()) % len(_AVATAR_PALETTE) if name else 0
            n = _color_counts.get(idx, 0)
            _color_counts[idx] = n + 1
            return n % 2

        use_app_name = self._active_tab != "web"
        for key, group in sorted(grouped.items(), key=lambda x: x[0].lower()):
            is_manual = key in standalone or (group and bool(group[0].get("group_name")))
            if use_app_name:
                display = key if is_manual else ((group[0].get("app_name") or key) if group else key)
                self._render_group(display, group, group_key=key,
                                   variant=_variant(display), is_manual=is_manual)
            else:
                self._render_group(key, group, variant=_variant(key), is_manual=is_manual)

        ctk.CTkFrame(self._scroll, height=14, fg_color="transparent").pack()
        tab_total = len(active)
        self._count_lbl.configure(
            text=f"{len(active)} of {tab_total}" if query else f"{tab_total} saved"
        )
        self._update_collapse_btn()

    def _section_label(self, text: str) -> None:
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(14, 3))
        ctk.CTkLabel(
            row, text=text,
            font=("Segoe UI", 10, "bold"), text_color=SECTION_FG,
        ).pack(side="left")
        ctk.CTkFrame(row, height=1, fg_color=BORDER, corner_radius=0).pack(
            side="left", fill="x", expand=True, padx=(8, 0),
        )

    def _render_group(self, display_name: str, group: list, group_key: str | None = None,
                      variant: int = 0, is_manual: bool = False) -> None:
        key = group_key or display_name
        # A single credential that belongs to no named group → plain card
        if len(group) == 1 and not is_manual:
            CredentialCard(
                self._scroll, group[0],
                on_copy=self._copy_password,
                on_edit=self._open_edit,
                on_delete=self._do_delete,
                avatar_variant=variant,
            ).pack(fill="x", padx=14, pady=(4, 0))
        else:
            # Named or multi-credential group (may be empty)
            self._group_keys.append(key)
            # Empty groups start expanded so the drop hint is visible
            default_collapsed = len(group) > 0
            CredentialGroup(
                self._scroll,
                display_name=display_name,
                group_key=key,
                creds=group,
                collapsed=self._collapsed.get(key, default_collapsed),
                on_toggle=self._on_group_toggle,
                on_copy=self._copy_password,
                on_edit=self._open_edit,
                on_delete=self._do_delete,
                avatar_variant=variant,
                on_register_drop=self._register_drop_zone if is_manual else None,
                is_manual=is_manual,
                on_delete_group=self._delete_group if is_manual else None,
            ).pack(fill="x", padx=14, pady=(4, 0))

    def _on_group_toggle(self, key: str, collapsed: bool) -> None:
        self._collapsed[key] = collapsed
        self._update_collapse_btn()

    # ── Group creation ────────────────────────────────────────────────────────

    def _open_new_group(self) -> None:
        GroupNameDialog(self._app, on_create=self._on_create_group)

    def _on_create_group(self, name: str) -> None:
        try:
            self._app.vault.create_group(name, self._active_tab)
        except Exception:
            pass
        self._refresh()

    def _delete_group(self, group_key: str) -> None:
        if messagebox.askyesno(
            "Delete Group",
            f"Delete group '{group_key}'?\n\n"
            "All credentials inside will be moved back to the main list.",
            icon="warning", parent=self._app,
        ):
            self._app.vault.delete_group(group_key, self._active_tab)
            self._refresh()

    # ── Custom-tab management ────────────────────────────────────────────────

    def _open_add_tab(self) -> None:
        GroupNameDialog(
            self._app, on_create=self._do_add_tab,
            title="New Tab", header="New Tab",
            placeholder="e.g. PIN Codes, Wi-Fi, Notes…",
        )

    def _do_add_tab(self, name: str) -> None:
        if name in self._BUILTIN_LABELS:
            return
        try:
            self._app.vault.create_custom_tab(name)
        except Exception:
            pass
        self._active_tab = name
        self._rebuild_tab_row()
        self._refresh()

    def _remove_tab(self, tab_name: str) -> None:
        if not messagebox.askyesno(
            "Remove Tab",
            f"Remove tab '{tab_name}'?\n\n"
            "Its credentials will be moved to Websites.",
            icon="warning", parent=self._app,
        ):
            return
        self._app.vault.delete_custom_tab(tab_name)
        if self._active_tab == tab_name:
            self._active_tab = "web"
        self._rebuild_tab_row()
        self._switch_tab(self._active_tab)

    # ── Generator dialog ─────────────────────────────────────────────────────

    def _open_generator(self) -> None:
        GeneratorDialog(self._app)

    # ── Drag-to-group ─────────────────────────────────────────────────────────

    def _register_drop_zone(self, key: str, widget) -> None:
        self._drop_zones.append((key, widget))

    def _drag_start(self, cred: dict) -> None:
        # Floating label via place() inside the main window — no new Toplevel,
        # no focus steal, no broken mouse capture.
        if self._drag_label and self._drag_label.winfo_exists():
            self._drag_label.destroy()
        self._drag_label = tk.Frame(
            self._app, bg=SURFACE,
            highlightthickness=1, highlightbackground=BORDER_HL,
        )
        tk.Label(
            self._drag_label, text=f"  {cred['username']}  ",
            bg=SURFACE, fg=TEXT, font=("Segoe UI", 11), pady=4,
        ).pack()
        self._drag_label.place(x=-300, y=-300)  # off-screen until first motion
        self._drag_label.lift()


    def _drag_motion(self, x_root: int, y_root: int) -> None:
        if self._drag_label and self._drag_label.winfo_exists():
            x = x_root - self._app.winfo_rootx() + 14
            y = y_root - self._app.winfo_rooty() + 14
            self._drag_label.place(x=x, y=y)

    def _drag_end(self, cred: dict, x_root: int, y_root: int) -> None:
        if self._drag_label and self._drag_label.winfo_exists():
            self._drag_label.destroy()
        self._drag_label = None

        # Check drop targets
        for key, w in self._drop_zones:
            try:
                wx, wy = w.winfo_rootx(), w.winfo_rooty()
                ww, wh = w.winfo_width(), w.winfo_height()
                if wx <= x_root <= wx + ww and wy <= y_root <= wy + wh:
                    if key != (cred.get("group_name") or cred["domain"]):
                        self._app.vault.update(cred["id"], group_name=key)
                        self._refresh()
                    return
            except Exception:
                pass

        # Dropped outside any group — remove from manual group if it was in one
        if cred.get("group_name"):
            self._app.vault.update(cred["id"], group_name="")
            self._refresh()

    def _update_collapse_btn(self) -> None:
        if not self._group_keys:
            self._collapse_btn.configure(text="Expand All", text_color=SECTION_FG)
            return
        all_collapsed = all(self._collapsed.get(k, True) for k in self._group_keys)
        self._collapse_btn.configure(
            text="Expand All" if all_collapsed else "Collapse All",
            text_color=TEXT2,
        )

    def _toggle_all_groups(self) -> None:
        if not self._group_keys:
            return
        any_expanded = any(not self._collapsed.get(k, True) for k in self._group_keys)
        new_state = any_expanded   # collapse all if any open; expand all if all closed
        for k in self._group_keys:
            self._collapsed[k] = new_state
        self._refresh()

    # ── Global drag handlers ──────────────────────────────────────────────────

    def _find_drag_cred(self, widget) -> dict | None:
        """Walk up the widget tree to find the credential being dragged."""
        w = widget
        while w is not None:
            if isinstance(w, ctk.CTkButton):
                return None  # buttons handle their own clicks
            if isinstance(w, CredentialCard):
                return w._cred
            if hasattr(w, '_drag_cred_tag'):
                return w._drag_cred_tag
            try:
                w = w.master
            except AttributeError:
                break
        return None

    def _on_global_press(self, e) -> None:
        self._drag_cred    = self._find_drag_cred(e.widget)
        self._drag_press_xy = (e.x_root, e.y_root) if self._drag_cred else None
        self._drag_active  = False

    def _on_global_motion(self, e) -> None:
        if not self._drag_cred or not self._drag_press_xy:
            return
        if not self._drag_active:
            dx = abs(e.x_root - self._drag_press_xy[0])
            dy = abs(e.y_root - self._drag_press_xy[1])
            if dx > 6 or dy > 6:
                self._drag_active = True
                self._drag_start(self._drag_cred)
        if self._drag_active:
            self._drag_motion(e.x_root, e.y_root)

    def _on_global_release(self, e) -> None:
        if self._drag_active and self._drag_cred is not None:
            self._drag_end(self._drag_cred, e.x_root, e.y_root)
        self._drag_cred     = None
        self._drag_press_xy = None
        self._drag_active   = False

    # ── DB watcher ────────────────────────────────────────────────────────────

    def _get_db_hash(self) -> str:
        try:
            creds = self._app.vault.get_all()
            payload = str([(c["id"], c["updated_at"]) for c in creds])
            return hashlib.md5(payload.encode()).hexdigest()
        except Exception:
            return ""

    def _poll_db(self) -> None:
        try:
            h = self._get_db_hash()
            if h and h != self._db_hash:
                self._db_hash = h
                self._refresh()
        except Exception:
            pass
        self.after(2000, self._poll_db)

    # ── Clipboard ─────────────────────────────────────────────────────────────

    def _copy_password(self, cred: dict, card=None) -> None:
        if self._clip_timer:
            self._clip_timer.cancel()
        self.clipboard_clear()
        self.clipboard_append(cred["password"])
        self.update()
        if card is not None:
            card.flash_copied()
        self._clip_timer = threading.Timer(
            CLIP_TTL, lambda: self.after(0, self._clear_clip)
        )
        self._clip_timer.daemon = True
        self._clip_timer.start()

    def _clear_clip(self) -> None:
        try:
            self.clipboard_clear()
            self.update()
        except Exception:
            pass

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def _open_add(self) -> None:
        preset = {"web": "Website", "app": "App"}.get(self._active_tab, self._active_tab)
        CredentialDialog(
            self._app, on_save=self._on_add,
            existing_groups=self._get_existing_groups(),
            custom_tabs=self._custom_tabs,
            preset_type=preset,
        )

    def _open_edit(self, cred: dict) -> None:
        CredentialDialog(
            self._app, existing=cred,
            on_save=lambda d: self._on_edit(cred["id"], d),
            existing_groups=self._get_existing_groups(),
            custom_tabs=self._custom_tabs,
        )

    def _get_existing_groups(self) -> list[str]:
        try:
            from_creds = {c["group_name"] for c in self._app.vault.get_all() if c.get("group_name")}
            from_table: set[str] = set()
            for t in ["web", "app"] + list(self._custom_tabs):
                from_table |= set(self._app.vault.get_groups(t))
            return sorted(from_creds | from_table)
        except Exception:
            return []

    def _on_add(self, data: dict) -> None:
        group = data.get("group_name", "")
        ct    = data.get("cred_type", "web")
        if ct == "app":
            self._app.vault.save_app(data["app_name"], data["username"], data["password"], group)
        elif ct == "web":
            self._app.vault.save(data["url"], data["username"], data["password"], group)
        else:
            self._app.vault.save_to_tab(data["app_name"], data["username"], data["password"], ct, group)
        self._refresh()

    def _on_edit(self, cred_id: int, data: dict) -> None:
        target  = data.get("target_tab")
        source  = data.get("cred_type", "web")
        new_ct  = target if (target and target != source) else None

        # When moving to Apps and app_name is still empty, use the best available label
        explicit_app_name = data.get("app_name") or None
        if new_ct == "app" and not explicit_app_name:
            # Find the existing credential to derive a name from
            try:
                existing = self._app.vault.get_by_id(cred_id)
                explicit_app_name = (
                    existing.get("app_name")
                    or existing.get("domain")
                    or ""
                ) or None
            except Exception:
                pass

        self._app.vault.update(
            cred_id,
            username=data.get("username"),
            password=data.get("password"),
            url=data.get("url") or None,
            app_name=explicit_app_name,
            group_name=data.get("group_name"),
            cred_type=new_ct,
        )
        self._refresh()

    def _do_delete(self, cred: dict) -> None:
        name = cred.get("app_name") or cred["domain"]
        if messagebox.askyesno(
            "Delete",
            f"Delete the saved login for  {name}?\n\nThis cannot be undone.",
            icon="warning", parent=self._app,
        ):
            self._app.vault.delete(cred["id"])
            self._refresh()

    def _import_csv(self) -> None:
        path = filedialog.askopenfilename(
            parent=self._app,
            title="Import Passwords",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except Exception as exc:
            messagebox.showerror("Import Failed", str(exc), parent=self._app)
            return

        if not rows:
            messagebox.showinfo("Import", "No data found in the CSV file.",
                                parent=self._app)
            return

        # ── Flexible column detection (case-insensitive, partial match) ──
        headers = list(rows[0].keys())

        def _col(*candidates) -> str | None:
            hl = {h.lower(): h for h in headers}
            for c in candidates:
                if c in hl:
                    return hl[c]
            for c in candidates:
                for hl_key, h in hl.items():
                    if c in hl_key:
                        return h
            return None

        col_url   = _col("url", "website", "web site", "site", "login_uri")
        col_user  = _col("username", "login", "email", "user", "login_username")
        col_pass  = _col("password", "pass", "login_password")
        col_title = _col("title", "name", "account", "label")
        col_type  = _col("type")

        if not col_user or not col_pass:
            messagebox.showerror(
                "Import Failed",
                "Could not find username/password columns.\n\n"
                f"Columns found: {', '.join(headers)}",
                parent=self._app,
            )
            return

        imported = skipped = 0
        for row in rows:
            username = row.get(col_user, "").strip()
            password = row.get(col_pass, "").strip()
            url      = row.get(col_url,   "").strip() if col_url   else ""
            title    = row.get(col_title, "").strip() if col_title else ""
            raw_type = row.get(col_type,  "").strip().lower() if col_type else ""

            if not username or not password:
                skipped += 1
                continue

            cred_type = raw_type if raw_type in ("web", "app") else (
                "app" if (not url and title) else "web"
            )

            try:
                if cred_type == "app":
                    self._app.vault.save_app(title or username, username, password)
                else:
                    if not url:
                        skipped += 1
                        continue
                    self._app.vault.save(url, username, password)
                imported += 1
            except Exception:
                skipped += 1

        self._refresh()
        skip_note = f"\nSkipped {skipped} rows (missing required fields)." if skipped else ""
        messagebox.showinfo(
            "Import Complete",
            f"Imported {imported} credential{'s' if imported != 1 else ''}."
            + skip_note,
            parent=self._app,
        )

    def _export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self._app,
            title="Export Passwords",
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv")],
            initialfile="passwords.csv",
        )
        if not path:
            return
        try:
            creds = self._app.vault.get_all()
        except VaultError:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Title", "URL", "Username", "Password", "Notes", "Type"])
            for c in creds:
                title = c.get("app_name") or c["domain"]
                writer.writerow([title, c["url"], c["username"], c["password"], "", c.get("cred_type", "web")])
        messagebox.showinfo(
            "Export complete",
            f"Exported {len(creds)} password{'s' if len(creds) != 1 else ''} to:\n{path}\n\n"
            "⚠  Delete the file once done — it contains your passwords in plain text.",
            parent=self._app,
        )

    def _show_transfer_menu(self) -> None:
        menu = tk.Menu(
            self._app, tearoff=0,
            bg=SURFACE, fg=TEXT,
            activebackground=ACCENT, activeforeground="#ffffff",
            font=FONT_SM, bd=0,
        )
        menu.add_command(label="  Import from CSV", command=self._import_csv)
        menu.add_command(label="  Export to CSV",   command=self._export_csv)
        menu.add_separator()
        menu.add_command(label="  Send to Phone",   command=self._send_to_phone)
        btn = self._transfer_btn
        menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height() + 2)

    def _send_to_phone(self) -> None:
        SendToPhoneDialog(self._app, self._app.vault)

    def _show_info(self) -> None:
        InfoDialog(self._app)



# ── Shared UI helpers ─────────────────────────────────────────────────────────

def _flash_copy_btn(btn: ctk.CTkButton) -> None:
    """Briefly show a 'Copied' state on a Copy button, then revert."""
    try:
        btn.configure(text="✓  Copied", fg_color=GREEN, text_color="#111827")
        btn.after(1600, lambda: (
            btn.configure(text="Copy", fg_color=ACCENT, text_color="#ffffff")
            if btn.winfo_exists() else None
        ))
    except Exception:
        pass


def _bind_hover(widget: ctk.CTkFrame) -> None:
    """Attach enter/leave highlight behaviour to a card or group widget."""
    def enter(e):
        prev = _hover_state["item"]
        if prev is not None and prev is not widget:
            try:
                if prev.winfo_exists():
                    prev.configure(fg_color=SURFACE, border_color=BORDER)
            except Exception:
                pass
        _hover_state["item"] = widget
        widget.configure(fg_color=SURFACE_HV, border_color=BORDER_HL)

    def leave(e):
        try:
            wx, wy = widget.winfo_rootx(), widget.winfo_rooty()
            ww, wh = widget.winfo_width(), widget.winfo_height()
            if not (wx <= e.x_root <= wx + ww and wy <= e.y_root <= wy + wh):
                widget.configure(fg_color=SURFACE, border_color=BORDER)
                if _hover_state["item"] is widget:
                    _hover_state["item"] = None
        except Exception:
            pass

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")


def _make_cred_actions(parent, on_edit, on_delete, cred) -> tuple:
    """Build the Edit/Delete buttons and an unconfigured Copy button; returns (frame, copy_btn)."""
    acts = ctk.CTkFrame(parent, fg_color="transparent")
    copy_btn = ctk.CTkButton(
        acts, text="Copy", width=66, height=32, font=FONT_SM, corner_radius=7,
        fg_color=ACCENT, hover_color=ACCENT_HV, text_color="#ffffff",
    )
    copy_btn.pack(side="left", padx=(0, 5))
    ctk.CTkButton(
        acts, text="Edit", width=54, height=32, font=FONT_SM, corner_radius=7,
        fg_color="transparent", border_width=1, border_color=BORDER,
        hover_color=SURFACE_HV, text_color=TEXT2,
        command=lambda: on_edit(cred),
    ).pack(side="left", padx=(0, 5))
    ctk.CTkButton(
        acts, text="✕", width=32, height=32, font=FONT_SM, corner_radius=7,
        fg_color="transparent", border_width=1, border_color=DANGER,
        hover_color="#2a0a0a", text_color=DANGER,
        command=lambda: on_delete(cred),
    ).pack(side="left")
    return acts, copy_btn


# ── Base dialog ────────────────────────────────────────────────────────────────

class _BaseDialog(ctk.CTkToplevel):
    """Common scaffolding shared by all modal dialogs."""

    def __init__(self, parent, title: str, size: str | None = None) -> None:
        super().__init__(parent)
        self.configure(fg_color=BG)
        self.title(title)
        if size is not None:
            self.geometry(size)
        self.resizable(False, False)
        self.transient(parent)
        self.after(60, lambda: (self.lift(), self.grab_set(), self._center()))
        self.after(200, lambda: _apply_icon(self))

    def _center(self) -> None:
        self.update_idletasks()
        px = self.master.winfo_rootx() + self.master.winfo_width() // 2
        py = self.master.winfo_rooty() + self.master.winfo_height() // 2
        self.geometry(f"+{px - self.winfo_width() // 2}+{py - self.winfo_height() // 2}")


# ── Credential card (single entry) ────────────────────────────────────────────

class CredentialCard(ctk.CTkFrame):
    def __init__(self, parent, cred: dict, on_copy, on_edit, on_delete,
                 avatar_variant: int = 0, **kw):
        super().__init__(
            parent,
            fg_color=SURFACE, border_width=1, border_color=BORDER,
            corner_radius=10, **kw,
        )
        self._cred           = cred
        self._copy_btn       = None
        self._avatar_variant = avatar_variant
        self._build(on_copy, on_edit, on_delete)

    def _build(self, on_copy, on_edit, on_delete) -> None:
        pad = ctk.CTkFrame(self, fg_color="transparent")
        pad.pack(fill="x", padx=14, pady=11)

        label = self._cred.get("app_name") or self._cred["domain"]
        av = ctk.CTkFrame(pad, width=44, height=44, corner_radius=10,
                          fg_color=_avatar_color(label, self._avatar_variant))
        av.pack(side="left", padx=(0, 14))
        av.pack_propagate(False)
        ctk.CTkLabel(av, text=_initial(label),
                     font=("Segoe UI", 17, "bold"), text_color="#ffffff").pack(expand=True)

        info = ctk.CTkFrame(pad, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(info, text=label, font=FONT_BOLD, text_color=TEXT, anchor="w").pack(fill="x")
        ctk.CTkLabel(info, text=self._cred["username"],
                     font=FONT_SM, text_color=TEXT2, anchor="w").pack(fill="x")

        acts, self._copy_btn = _make_cred_actions(pad, on_edit, on_delete, self._cred)
        self._copy_btn.configure(command=lambda: on_copy(self._cred, self))
        acts.pack(side="right", padx=(10, 0))

        _bind_hover(self)

    def flash_copied(self) -> None:
        if self._copy_btn:
            _flash_copy_btn(self._copy_btn)



# ── Credential group (collapsible multi-login) ────────────────────────────────

class CredentialGroup(ctk.CTkFrame):
    """Collapsible group card for multiple credentials sharing the same domain/app."""

    def __init__(
        self, parent,
        display_name: str,
        group_key: str,
        creds: list,
        collapsed: bool,
        on_toggle,
        on_copy,
        on_edit,
        on_delete,
        avatar_variant: int = 0,
        on_register_drop=None,
        is_manual: bool = False,
        on_delete_group=None,
        **kw,
    ):
        super().__init__(
            parent,
            fg_color=SURFACE, border_width=1, border_color=BORDER,
            corner_radius=10, **kw,
        )
        self._display          = display_name
        self._key              = group_key
        self._creds            = creds
        self._collapsed        = collapsed
        self._on_toggle        = on_toggle
        self._on_copy          = on_copy
        self._on_edit          = on_edit
        self._on_delete        = on_delete
        self._avatar_variant   = avatar_variant
        self._on_register_drop = on_register_drop
        self._is_manual        = is_manual
        self._on_delete_group  = on_delete_group
        self._body_built       = False
        self._build()

    def _build(self) -> None:
        # ── Group header ──
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 10))
        if self._on_register_drop:
            self.after(150, lambda: self._on_register_drop(self._key, self))

        av = ctk.CTkFrame(hdr, width=44, height=44, corner_radius=10,
                          fg_color=_avatar_color(self._display, self._avatar_variant))
        av.pack(side="left", padx=(0, 14))
        av.pack_propagate(False)
        av_lbl = ctk.CTkLabel(av, text=_initial(self._display),
                     font=("Segoe UI", 17, "bold"), text_color="#ffffff")
        av_lbl.pack(expand=True)

        info = ctk.CTkFrame(hdr, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        info_title = ctk.CTkLabel(info, text=self._display,
                                   font=FONT_BOLD, text_color=TEXT, anchor="w")
        info_title.pack(fill="x")
        n = len(self._creds)
        count_text = "Empty — drag a login here" if n == 0 else f"{n} login{'s' if n != 1 else ''}"
        info_sub = ctk.CTkLabel(info, text=count_text,
                     font=FONT_SM, text_color=TEXT2, anchor="w")
        info_sub.pack(fill="x")

        if self._is_manual and self._on_delete_group:
            ctk.CTkButton(
                hdr, text="✕", width=32, height=32, font=FONT_SM, corner_radius=7,
                fg_color="transparent", border_width=1, border_color=DANGER,
                hover_color="#2a0a0a", text_color=DANGER,
                command=lambda: self._on_delete_group(self._key),
            ).pack(side="right")

        self._chevron = ctk.CTkButton(
            hdr,
            text="▲" if not self._collapsed else "▼",
            width=32, height=32, font=FONT_SM, corner_radius=7,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE_HV, text_color=TEXT2,
            command=self._toggle,
        )
        self._chevron.pack(side="right", padx=(0, 6) if (self._is_manual and self._on_delete_group) else 0)

        # Clicking anywhere on the header (outside the action buttons) toggles the group.
        for w in (hdr, av, av_lbl, info, info_title, info_sub):
            w.bind("<ButtonRelease-1>", lambda e: self._toggle(), add="+")
            try:
                w.configure(cursor="hand2")
            except Exception:
                pass

        _bind_hover(self)

        # ── Separator + body ──
        self._sep  = ctk.CTkFrame(self, height=1, fg_color=BORDER, corner_radius=0)
        self._body = ctk.CTkFrame(self, fg_color="transparent")

        if not self._collapsed:
            self._sep.pack(fill="x")
            self._body.pack(fill="x")
            self._build_body()
            self._body_built = True

    def _build_body(self) -> None:
        if not self._creds:
            ctk.CTkLabel(
                self._body,
                text="Drag a credential here to add it to this group",
                font=FONT_SM, text_color=TEXT2, justify="center",
            ).pack(pady=16)
            return
        for i, cred in enumerate(self._creds):
            last = i == len(self._creds) - 1

            row = ctk.CTkFrame(self._body, fg_color="transparent")
            row.pack(fill="x", pady=(8 if i == 0 else 2, 8 if last else 2))
            row._drag_cred_tag = cred  # picked up by MainFrame._find_drag_cred

            # Fixed-size accent indent bar — no fill="y" to avoid height inflation
            accent = ctk.CTkFrame(row, width=3, height=28, fg_color=ACCENT, corner_radius=2)
            accent.pack_propagate(False)
            accent.pack(side="left", padx=(32, 0))

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, padx=(10, 0))
            ctk.CTkLabel(
                info, text=cred["username"], font=FONT, text_color=TEXT, anchor="w",
            ).pack(fill="x")

            acts, copy_btn = _make_cred_actions(row, self._on_edit, self._on_delete, cred)
            copy_btn.configure(command=lambda c=cred, b=copy_btn: self._copy_cred(c, b))
            acts.pack(side="right", padx=(0, 14))

    def _copy_cred(self, cred: dict, btn: ctk.CTkButton) -> None:
        self._on_copy(cred, None)
        _flash_copy_btn(btn)

    def _toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._on_toggle(self._key, self._collapsed)
        self._chevron.configure(text="▼" if self._collapsed else "▲")
        if self._collapsed:
            self._sep.pack_forget()
            self._body.pack_forget()
        else:
            if not self._body_built:
                self._build_body()
                self._body_built = True
            self._sep.pack(fill="x")
            self._body.pack(fill="x")


# ── Add / Edit dialog ─────────────────────────────────────────────────────────

class CredentialDialog(_BaseDialog):
    def __init__(self, parent: App, on_save, existing: dict | None = None,
                 existing_groups: list | None = None,
                 preset_group: str = "",
                 custom_tabs: list | None = None,
                 preset_type: str | None = None) -> None:
        super().__init__(
            parent,
            "Edit Credential" if existing else "Add Credential",
            "460x565" if existing else "460x520",
        )
        self._on_save         = on_save
        self._existing        = existing
        self._existing_groups = existing_groups or []
        self._preset_group    = preset_group
        self._custom_tabs     = custom_tabs or []
        self._preset_type     = preset_type
        self._build()

        if existing:
            cred_type = existing.get("cred_type", "web")
            if cred_type == "web":
                self._type_var.set("Website")
                self._set_type("Website")
                # If the credential has no URL (e.g. moved from a custom tab),
                # fall back to app_name so the field isn't confusingly blank.
                url_val = existing.get("url") or existing.get("app_name", "")
                self._url_e.insert(0, url_val)
            elif cred_type == "app":
                self._type_var.set("App")
                self._set_type("App")
                self._app_e.insert(0, existing.get("app_name", ""))
            else:
                self._type_var.set(cred_type)
                self._set_type(cred_type)
                self._app_e.insert(0, existing.get("app_name", ""))
            self._user_e.insert(0, existing.get("username", ""))
            self._pw_e.insert(0, existing.get("password", ""))
            grp = existing.get("group_name", "")
            self._group_combo.set(grp if grp else "(none)")
            self._type_menu.configure(state="disabled")

        if self._preset_group:
            self._group_combo.set(self._preset_group)

        if not existing and self._preset_type:
            self._type_var.set(self._preset_type)
            self._set_type(self._preset_type)

        ct = existing.get("cred_type", "web") if existing else None
        active_e = self._app_e if (ct and ct != "web") else self._url_e
        self.after(80, active_e.focus_set)

    def _build(self) -> None:
        hdr = ctk.CTkFrame(self, height=54, fg_color=HEADER_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr,
            text="Edit Credential" if self._existing else "New Credential",
            font=FONT_BOLD, text_color=TEXT,
        ).pack(side="left", padx=20, pady=16)

        body = ctk.CTkFrame(self, fg_color=BG)
        body.pack(fill="both", expand=True, padx=22, pady=14)

        # Type selector
        type_row = ctk.CTkFrame(body, fg_color="transparent")
        type_row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(type_row, text="Type", font=FONT_SM, text_color=TEXT2,
                     width=80, anchor="w").pack(side="left")
        all_types = ["Website", "App"] + self._custom_tabs
        self._type_var = tk.StringVar(value="Website")
        self._type_menu = ctk.CTkOptionMenu(
            type_row, values=all_types,
            variable=self._type_var,
            font=FONT_SM, height=36,
            fg_color=SURFACE, button_color=BORDER,
            button_hover_color=SURFACE_HV, text_color=TEXT,
            dropdown_fg_color=SURFACE, dropdown_text_color=TEXT,
            dropdown_hover_color=SURFACE_HV,
            command=self._set_type,
        )
        self._type_menu.pack(side="left", fill="x", expand=True)

        # Label + swappable entry container (URL ↔ App Name)
        self._url_label = ctk.CTkLabel(body, text="URL", font=FONT_SM, text_color=TEXT2, anchor="w")
        self._url_label.pack(fill="x", pady=(0, 2))

        # Container holds exactly one entry at a time; _set_type swaps them
        self._id_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._id_frame.pack(fill="x", pady=(0, 2))

        self._url_e = ctk.CTkEntry(
            self._id_frame, placeholder_text="https://github.com",
            height=40, font=FONT,
            fg_color=INPUT_BG, border_color=BORDER,
            border_width=1, corner_radius=8, text_color=TEXT,
        )
        self._url_e.pack(fill="x")   # visible by default (Website type)

        self._app_e = ctk.CTkEntry(
            self._id_frame, placeholder_text="Discord, Slack, Steam…",
            height=40, font=FONT,
            fg_color=INPUT_BG, border_color=BORDER,
            border_width=1, corner_radius=8, text_color=TEXT,
        )
        # NOT packed yet — shown only when type = App

        self._app_hint = ctk.CTkLabel(
            body,
            text="Enter the app's window title exactly (e.g. 'Discord', 'Slack').\n"
                 f"{_get_hotkey_label()} will autofill when that window is active.",
            font=("Segoe UI", 10), text_color=TEXT2,
            wraplength=380, justify="left",
        )
        # NOT packed yet — shown only when type = App

        self._user_e = self._field(body, "Username / Email")

        pw_row = ctk.CTkFrame(body, fg_color="transparent")
        pw_row.pack(fill="x", pady=(0, 4))

        self._pw_e = ctk.CTkEntry(
            pw_row, placeholder_text="Password",
            height=40, font=FONT, show="•",
            fg_color=INPUT_BG, border_color=BORDER,
            border_width=1, corner_radius=8, text_color=TEXT,
        )
        self._pw_e.pack(side="left", fill="x", expand=True)

        self._eye = ctk.CTkButton(
            pw_row, text="Show", width=60, height=40, font=FONT_SM, corner_radius=8,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE, text_color=TEXT2,
            command=self._toggle_pw,
        )
        self._eye.pack(side="left", padx=(6, 0))

        self._err = ctk.CTkLabel(body, text="", text_color=DANGER, font=FONT_SM)
        self._err.pack(anchor="w", pady=(2, 0))

        # Tab (only shown when editing — lets the user move the credential to any tab)
        self._tab_move_var: tk.StringVar | None = None
        if self._existing:
            ct = self._existing.get("cred_type", "web")
            tab_options = ["Websites", "Apps"] + self._custom_tabs
            current_tab = {"web": "Websites", "app": "Apps"}.get(ct, ct)

            tab_row = ctk.CTkFrame(body, fg_color="transparent")
            tab_row.pack(fill="x", pady=(8, 0))
            ctk.CTkLabel(tab_row, text="Tab", font=FONT_SM, text_color=TEXT2,
                         anchor="w", width=80).pack(side="left")
            self._tab_move_var = tk.StringVar(value=current_tab)
            ctk.CTkOptionMenu(
                tab_row, values=tab_options,
                variable=self._tab_move_var,
                font=FONT_SM, height=40,
                fg_color=INPUT_BG, button_color=BORDER,
                button_hover_color=SURFACE_HV, text_color=TEXT,
                dropdown_fg_color=SURFACE, dropdown_text_color=TEXT,
                dropdown_hover_color=SURFACE_HV,
            ).pack(side="left", fill="x", expand=True)

        # Group
        group_row = ctk.CTkFrame(body, fg_color="transparent")
        group_row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(group_row, text="Group", font=FONT_SM, text_color=TEXT2,
                     anchor="w", width=80).pack(side="left")
        combo_values = ["(none)"] + self._existing_groups
        self._group_combo = ctk.CTkComboBox(
            group_row, values=combo_values,
            height=40, font=FONT,
            fg_color=INPUT_BG, border_color=BORDER,
            border_width=1, corner_radius=8,
            text_color=TEXT,
            button_color=BORDER, button_hover_color=SURFACE_HV,
            dropdown_fg_color=SURFACE, dropdown_text_color=TEXT,
            dropdown_hover_color=SURFACE_HV,
        )
        self._group_combo.set("(none)")
        self._group_combo.pack(side="left", fill="x", expand=True)

        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill="x", pady=(10, 0))

        ctk.CTkButton(
            btn_row, text="Cancel", width=120, height=40, font=FONT,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE, text_color=TEXT2,
            command=self.destroy,
        ).pack(side="left")

        ctk.CTkButton(
            btn_row, text="Save", width=120, height=40, font=FONT_BOLD,
            fg_color=ACCENT, hover_color=ACCENT_HV,
            command=self._submit,
        ).pack(side="right")

    def _set_type(self, value: str) -> None:
        if value == "Website":
            self._url_label.configure(text="URL")
            self._app_e.pack_forget()
            self._app_hint.pack_forget()
            self._url_e.pack(fill="x")
        else:
            field_label = "App Name" if value == "App" else "Label"
            placeholder = "Discord, Slack, Steam…" if value == "App" else "e.g. Netflix, Bank…"
            self._url_label.configure(text=field_label)
            self._url_e.pack_forget()
            self._app_e.configure(placeholder_text=placeholder)
            self._app_e.pack(fill="x")
            if value == "App":
                self._app_hint.pack(fill="x", pady=(0, 6))
            else:
                self._app_hint.pack_forget()

    def _field(self, parent, placeholder: str) -> ctk.CTkEntry:
        e = ctk.CTkEntry(
            parent, placeholder_text=placeholder,
            height=40, font=FONT,
            fg_color=INPUT_BG, border_color=BORDER,
            border_width=1, corner_radius=8, text_color=TEXT,
        )
        e.pack(fill="x", pady=(0, 6))
        return e

    def _toggle_pw(self) -> None:
        hidden = self._pw_e.cget("show") == "•"
        self._pw_e.configure(show="" if hidden else "•")
        self._eye.configure(text="Hide" if hidden else "Show")

    def _submit(self) -> None:
        if self._existing:
            cred_type = self._existing.get("cred_type", "web")
        else:
            type_val  = self._type_var.get()
            cred_type = {"Website": "web", "App": "app"}.get(type_val, type_val)

        identifier = (self._url_e if cred_type == "web" else self._app_e).get().strip()
        user = self._user_e.get().strip()
        pw   = self._pw_e.get()

        if not identifier and not self._existing:
            msgs = {"web": "URL is required.", "app": "App name is required."}
            self._err.configure(text=msgs.get(cred_type, "Label is required."))
            return
        if not user:
            self._err.configure(text="Username is required.")
            return
        if not pw:
            self._err.configure(text="Password is required.")
            return

        raw_group  = self._group_combo.get().strip()
        group_name = "" if raw_group == "(none)" else raw_group

        # Determine target tab (may differ from cred_type when editing)
        if self._tab_move_var is not None:
            tab_label  = self._tab_move_var.get()
            target_tab = {"Websites": "web", "Apps": "app"}.get(tab_label, tab_label)
        else:
            target_tab = cred_type

        self._on_save({
            "cred_type":  cred_type,   # source type (determines which field holds identifier)
            "target_tab": target_tab,  # where the credential should live after save
            "url":        identifier if cred_type == "web" else "",
            "app_name":   identifier if cred_type != "web" else "",
            "group_name": group_name,
            "username":   user,
            "password":   pw,
        })
        self.destroy()


# ── New-group dialog ──────────────────────────────────────────────────────────

class GroupNameDialog(_BaseDialog):
    """Prompts for a name (group or tab), then hands it back via on_create."""

    def __init__(self, parent: App, on_create,
                 title: str = "New Group",
                 header: str = "New Group",
                 placeholder: str = "e.g. Gaming, Work, Social…") -> None:
        super().__init__(parent, title, "360x230")
        self._on_create   = on_create
        self._header      = header
        self._placeholder = placeholder
        self._build()

    def _build(self) -> None:
        hdr = ctk.CTkFrame(self, height=48, fg_color=HEADER_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text=self._header, font=FONT_BOLD, text_color=TEXT).pack(
            side="left", padx=20
        )

        body = ctk.CTkFrame(self, fg_color=BG)
        body.pack(fill="both", expand=True, padx=22, pady=14)

        label_text = "Tab Name" if "Tab" in self._header else "Group Name"
        ctk.CTkLabel(body, text=label_text, font=FONT_SM, text_color=TEXT2,
                     anchor="w").pack(fill="x", pady=(0, 4))
        self._name_e = ctk.CTkEntry(
            body, placeholder_text=self._placeholder,
            height=40, font=FONT,
            fg_color=INPUT_BG, border_color=BORDER,
            border_width=1, corner_radius=8, text_color=TEXT,
        )
        self._name_e.pack(fill="x")

        self._err = ctk.CTkLabel(body, text="", text_color=DANGER, font=FONT_SM)
        self._err.pack(anchor="w", pady=(4, 0))

        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(
            btn_row, text="Cancel", width=100, height=36, font=FONT,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE, text_color=TEXT2, command=self.destroy,
        ).pack(side="left")
        ctk.CTkButton(
            btn_row, text="Create", width=100, height=36, font=FONT_BOLD,
            fg_color=ACCENT, hover_color=ACCENT_HV, command=self._submit,
        ).pack(side="right")

        self.after(80, self._name_e.focus_set)
        self.bind("<Return>", lambda _: self._submit())

    def _submit(self) -> None:
        name = self._name_e.get().strip()
        if not name:
            self._err.configure(text="Name is required.")
            return
        self._on_create(name)
        self.destroy()


# ── Password generator dialog ─────────────────────────────────────────────────

class GeneratorDialog(_BaseDialog):
    def __init__(self, parent: App) -> None:
        super().__init__(parent, "Password Generator", "440x490")
        self._build()

    def _build(self) -> None:
        hdr = ctk.CTkFrame(self, height=52, fg_color=HEADER_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Password Generator", font=FONT_BOLD, text_color=TEXT).pack(
            side="left", padx=20
        )

        body = ctk.CTkFrame(self, fg_color=BG)
        body.pack(fill="both", expand=True, padx=28, pady=20)

        out_row = ctk.CTkFrame(body, fg_color="transparent")
        out_row.pack(fill="x")
        self._output = ctk.CTkEntry(
            out_row, placeholder_text="Click Generate…",
            height=46, font=("Consolas", 13),
            fg_color=INPUT_BG, border_color=BORDER,
            border_width=1, corner_radius=8, text_color=TEXT,
        )
        self._output.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            out_row, text="Copy", width=80, height=46, font=FONT_SM, corner_radius=8,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE, text_color=TEXT2,
            command=self._copy,
        ).pack(side="left", padx=(10, 0))

        self._copied_lbl = ctk.CTkLabel(body, text="", font=FONT_SM, text_color=GREEN)
        self._copied_lbl.pack(anchor="e", pady=(4, 12))

        len_row = ctk.CTkFrame(body, fg_color="transparent")
        len_row.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(len_row, text="Length", font=FONT_SM, text_color=TEXT2,
                     width=70, anchor="w").pack(side="left")
        self._len_var = tk.IntVar(value=20)
        self._len_lbl = ctk.CTkLabel(len_row, text="20", font=FONT_BOLD,
                                      text_color=TEXT, width=30, anchor="e")
        self._len_lbl.pack(side="right")
        ctk.CTkSlider(
            len_row, from_=8, to=32, number_of_steps=24,
            variable=self._len_var,
            button_color=ACCENT, button_hover_color=ACCENT_HV,
            progress_color=ACCENT, fg_color=SURFACE,
            command=lambda v: self._len_lbl.configure(text=str(int(v))),
        ).pack(side="left", fill="x", expand=True, padx=(10, 10))

        card = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=10,
                            border_width=1, border_color=BORDER)
        card.pack(fill="x", pady=(0, 16))
        checks = ctk.CTkFrame(card, fg_color="transparent")
        checks.pack(fill="x", padx=18, pady=(12, 12))

        self._upper   = self._checkbox(checks, "Uppercase   (A–Z)")
        self._lower   = self._checkbox(checks, "Lowercase   (a–z)")
        self._digits  = self._checkbox(checks, "Numbers     (0–9)")
        self._symbols = self._checkbox(checks, "Symbols     (!@#…)")

        srow = ctk.CTkFrame(body, fg_color="transparent")
        srow.pack(fill="x", pady=(0, 16))
        self._strength_bar = ctk.CTkProgressBar(
            srow, height=6, corner_radius=3,
            fg_color=SURFACE, progress_color=BORDER,
        )
        self._strength_bar.set(0)
        self._strength_bar.pack(side="left", fill="x", expand=True)
        self._strength_lbl = ctk.CTkLabel(
            srow, text="", font=FONT_SM, text_color=TEXT2, width=82, anchor="e",
        )
        self._strength_lbl.pack(side="right", padx=(12, 0))

        ctk.CTkButton(
            body, text="Generate", height=44, font=FONT_BOLD,
            fg_color=ACCENT, hover_color=ACCENT_HV, corner_radius=8,
            command=self._generate,
        ).pack(fill="x")

    def _checkbox(self, parent, label: str) -> tk.BooleanVar:
        var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            parent, text=label, variable=var,
            font=FONT, text_color=TEXT2,
            fg_color=ACCENT, hover_color=ACCENT_HV,
            border_color=BORDER, checkmark_color="#ffffff",
            checkbox_width=18, checkbox_height=18, corner_radius=4,
        ).pack(anchor="w", pady=4)
        return var

    def _generate(self) -> None:
        pwd = _gen_password(
            self._len_var.get(),
            upper=self._upper.get(), lower=self._lower.get(),
            digits=self._digits.get(), symbols=self._symbols.get(),
        )
        if not pwd:
            return
        self._output.delete(0, "end")
        self._output.insert(0, pwd)
        pct, label, color = _password_strength(pwd)
        self._strength_bar.set(pct / 100)
        self._strength_bar.configure(progress_color=color)
        self._strength_lbl.configure(text=label, text_color=color)
        self._copied_lbl.configure(text="")

    def _copy(self) -> None:
        pwd = self._output.get()
        if not pwd:
            return
        self.clipboard_clear()
        self.clipboard_append(pwd)
        self.update()
        self._copied_lbl.configure(text="✓  Copied!")
        self.after(2000, lambda: (
            self._copied_lbl.configure(text="")
            if self._copied_lbl.winfo_exists() else None
        ))


# ── Autofill picker (multiple matches) ────────────────────────────────────────

class AppFillDialog(_BaseDialog):
    """Shown when Ctrl+Shift+F finds multiple credentials for the active app."""

    def __init__(self, parent: App, creds: list, hwnd: int) -> None:
        app_name = creds[0].get("app_name") or creds[0]["domain"]
        super().__init__(parent, f"Autofill — {app_name}")
        self._creds = creds
        self._hwnd  = hwnd
        self._build(app_name)

    def _build(self, app_name: str) -> None:
        hdr = ctk.CTkFrame(self, height=48, fg_color=HEADER_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text=f"Choose login for {app_name}",
                     font=FONT_BOLD, text_color=TEXT).pack(side="left", padx=20)

        body = ctk.CTkFrame(self, fg_color=BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        for cred in self._creds:
            row = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=8)
            row.pack(fill="x", pady=4)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=12, pady=8)

            ctk.CTkLabel(inner, text=cred["username"], font=FONT, text_color=TEXT,
                         anchor="w").pack(side="left", fill="x", expand=True)

            ctk.CTkButton(
                inner, text="Copy", width=62, height=30, font=FONT_SM, corner_radius=6,
                fg_color="transparent", border_width=1, border_color=BORDER,
                hover_color=SURFACE_HV, text_color=TEXT2,
                command=lambda c=cred: self._do_copy(c),
            ).pack(side="left", padx=(0, 6))

            ctk.CTkButton(
                inner, text="Fill", width=62, height=30, font=FONT_SM, corner_radius=6,
                fg_color=ACCENT, hover_color=ACCENT_HV,
                command=lambda c=cred: self._do_fill(c),
            ).pack(side="left")

        h = 48 + 12 + len(self._creds) * 64 + 12
        self.geometry(f"380x{h}")

    def _do_copy(self, cred: dict) -> None:
        self.clipboard_clear()
        self.clipboard_append(cred["password"])
        self.update()
        self.destroy()

    def _do_fill(self, cred: dict) -> None:
        hwnd     = self._hwnd
        username = cred["username"]
        password = cred["password"]
        self.destroy()

        def _fill():
            time.sleep(0.3)
            _autofill.focus_hwnd(hwnd)
            time.sleep(0.1)
            _autofill.type_credentials(username, password)

        threading.Thread(target=_fill, daemon=True).start()


# ── Send-to-Phone dialog ──────────────────────────────────────────────────────

class SendToPhoneDialog(_BaseDialog):
    _TIMEOUT = 60

    def __init__(self, parent: App, vault) -> None:
        super().__init__(parent, "Send to Phone", "320x460")
        self._server: _PhoneExportServer | None = None
        self._seconds = self._TIMEOUT
        self._done    = False
        self._start_server(vault)
        self._build()
        self.after(1000, self._tick)
        self.after(300,  self._poll)

    def _start_server(self, vault) -> None:
        port         = _find_free_port()
        token        = secrets.token_urlsafe(16)
        self._url    = f"http://{_get_local_ip()}:{port}/export/{token}"
        self._server = _PhoneExportServer(vault, token, port)

    def _build(self) -> None:
        hdr = ctk.CTkFrame(self, height=52, fg_color=HEADER_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Send to Phone", font=FONT_BOLD,
                     text_color=TEXT).pack(side="left", padx=20)

        body = ctk.CTkFrame(self, fg_color=BG)
        body.pack(fill="both", expand=True, padx=20, pady=16)

        ctk.CTkLabel(
            body,
            text="Make sure your phone is on the same network, then scan:",
            font=FONT_SM, text_color=TEXT2, wraplength=260, justify="center",
        ).pack(pady=(0, 14))

        try:
            import qrcode as _qr
            qr = _qr.QRCode(box_size=7, border=2,
                            error_correction=_qr.constants.ERROR_CORRECT_M)
            qr.add_data(self._url)
            qr.make(fit=True)
            pil_img = qr.make_image(fill_color=(0, 0, 0),
                                    back_color=(255, 255, 255)).convert("RGB")
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(220, 220))
            ctk.CTkLabel(body, image=ctk_img, text="").pack(pady=(0, 12))
        except Exception:
            ctk.CTkLabel(
                body,
                text="Install  qrcode[pil]  to display the QR code.",
                font=FONT_SM, text_color=TEXT2, wraplength=260, justify="center",
            ).pack(pady=(0, 12))

        url_e = ctk.CTkEntry(
            body, font=("Consolas", 9), height=28,
            fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT2,
        )
        url_e.insert(0, self._url)
        url_e.configure(state="readonly")
        url_e.pack(fill="x", pady=(0, 12))

        self._status_lbl = ctk.CTkLabel(
            body, text=f"Expires in {self._seconds}s",
            font=FONT_SM, text_color=TEXT2,
        )
        self._status_lbl.pack(pady=(0, 10))

        ctk.CTkButton(
            body, text="Cancel", height=36, font=FONT,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE, text_color=TEXT2,
            command=self.destroy,
        ).pack(fill="x")

    def _tick(self) -> None:
        if self._done or not self.winfo_exists():
            return
        self._seconds -= 1
        if self._seconds <= 0:
            self._finish(expired=True)
            return
        self._status_lbl.configure(text=f"Expires in {self._seconds}s")
        self.after(1000, self._tick)

    def _poll(self) -> None:
        if self._done or not self.winfo_exists():
            return
        if self._server and self._server.used.is_set():
            self._finish(expired=False)
            return
        self.after(300, self._poll)

    def _finish(self, *, expired: bool) -> None:
        self._done = True
        if not expired:
            self._status_lbl.configure(text="Downloaded!", text_color=GREEN)
            self.after(1800, self.destroy)
        else:
            self.destroy()

    def destroy(self) -> None:
        if self._server:
            self._server.shutdown()
        try:
            super().destroy()
        except Exception:
            pass


# ── Hotkey dialog ─────────────────────────────────────────────────────────────

class HotkeyDialog(_BaseDialog):
    """Captures a new global hotkey combination from a key press event."""

    _CTRL_MASK  = 0x0004
    _SHIFT_MASK = 0x0001
    _ALT_MASK   = 0x20000

    _MOD_KEYSYMS = frozenset({
        "Shift_L", "Shift_R", "Control_L", "Control_R",
        "Alt_L", "Alt_R", "Caps_Lock", "Super_L", "Super_R",
        "ISO_Level3_Shift",
    })

    def __init__(self, parent: App, current_label: str, on_save,
                 on_close=None) -> None:
        super().__init__(parent, "Change Autofill Hotkey", "420x310")
        self._on_save  = on_save
        self._on_close = on_close
        self._captured = None  # (mod_flags, vk, label_str)
        self._closed   = False
        self.protocol("WM_DELETE_WINDOW", self._do_close)
        self._build(current_label)

    def _do_close(self) -> None:
        if self._closed:
            return
        self._closed = True
        cb = self._on_close
        self._on_close = None
        self.destroy()
        if cb:
            cb()

    def _build(self, current_label: str) -> None:
        hdr = ctk.CTkFrame(self, height=48, fg_color=HEADER_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Change Autofill Hotkey",
                     font=FONT_BOLD, text_color=TEXT).pack(side="left", padx=20)

        body = ctk.CTkFrame(self, fg_color=BG)
        body.pack(fill="both", expand=True, padx=22, pady=14)

        ctk.CTkLabel(
            body, text=f"Current:  {current_label}",
            font=FONT_SM, text_color=TEXT2, anchor="w",
        ).pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            body,
            text="Click the box below, then press the desired combination.\n"
                 "Must include Ctrl, Shift, or Alt with a letter (A–Z) or F1–F12.",
            font=FONT_SM, text_color=TEXT2, anchor="w",
            justify="left", wraplength=370,
        ).pack(fill="x", pady=(0, 8))

        self._capture_frame = ctk.CTkFrame(
            body, fg_color=INPUT_BG, border_width=2,
            border_color=BORDER, corner_radius=8,
        )
        self._capture_frame.pack(fill="x")
        self._capture_lbl = ctk.CTkLabel(
            self._capture_frame,
            text="Click here, then press hotkey…",
            font=("Consolas", 13), text_color=TEXT2, height=52,
        )
        self._capture_lbl.pack(fill="x", padx=14)

        self._err = ctk.CTkLabel(body, text="", text_color=DANGER, font=FONT_SM)
        self._err.pack(anchor="w", pady=(6, 0))

        btn_row = ctk.CTkFrame(body, fg_color="transparent")
        btn_row.pack(fill="x", pady=(10, 0))
        ctk.CTkButton(
            btn_row, text="Cancel", width=110, height=38, font=FONT,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE, text_color=TEXT2,
            command=self._do_close,
        ).pack(side="left")
        self._ok_btn = ctk.CTkButton(
            btn_row, text="Save", width=110, height=38, font=FONT_BOLD,
            fg_color=BORDER, hover_color=BORDER, state="disabled",
            command=self._submit,
        )
        self._ok_btn.pack(side="right")

        for w in (self._capture_frame, self._capture_lbl):
            w.bind("<Button-1>", lambda e: self._activate_capture())
        self.bind("<KeyPress>", self._on_key)

    def _activate_capture(self) -> None:
        self._capture_frame.configure(border_color=ACCENT)
        self._capture_lbl.configure(text="Listening…", text_color=ACCENT)
        self.focus_set()

    def _on_key(self, event) -> None:
        if event.keysym in self._MOD_KEYSYMS:
            return

        mods: list[str] = []
        mod_flags = 0
        if event.state & self._CTRL_MASK:
            mods.append("Ctrl")
            mod_flags |= _autofill.MOD_CONTROL
        if event.state & self._SHIFT_MASK:
            mods.append("Shift")
            mod_flags |= _autofill.MOD_SHIFT
        if event.state & self._ALT_MASK:
            mods.append("Alt")
            mod_flags |= _autofill.MOD_ALT

        if not mods:
            self._capture_frame.configure(border_color=DANGER)
            self._err.configure(
                text="At least one modifier (Ctrl, Shift, Alt) is required."
            )
            return

        ks = event.keysym.upper()
        vk: int | None = None
        if len(ks) == 1 and ks.isalpha():
            vk = ord(ks)
        elif ks.startswith("F") and ks[1:].isdigit():
            n = int(ks[1:])
            if 1 <= n <= 12:
                vk = 0x6F + n

        if vk is None:
            self._capture_frame.configure(border_color=DANGER)
            self._err.configure(
                text=f"'{event.keysym}' is not supported. Use A–Z or F1–F12."
            )
            return

        label = "+".join(mods + [ks])
        self._captured = (mod_flags, vk, label)
        self._capture_frame.configure(border_color=GREEN)
        self._capture_lbl.configure(text=label, text_color=TEXT)
        self._ok_btn.configure(state="normal", fg_color=ACCENT, hover_color=ACCENT_HV)
        self._err.configure(text="")

    def _submit(self) -> None:
        if self._captured:
            self._on_save(*self._captured)
        self._do_close()


# ── Info dialog ───────────────────────────────────────────────────────────────

class InfoDialog(_BaseDialog):
    def __init__(self, parent: App) -> None:
        super().__init__(parent, "Info", "500x740")
        self._app = parent
        self._build()

    def _build(self) -> None:
        hdr = ctk.CTkFrame(self, height=52, fg_color=HEADER_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Info", font=FONT_BOLD, text_color=TEXT).pack(
            side="left", padx=20
        )

        body = ctk.CTkFrame(self, fg_color=BG)
        body.pack(fill="both", expand=True, padx=20, pady=14)

        # ── Device info ───────────────────────────────────────────────────────
        ctk.CTkLabel(
            body,
            text="Back up the device key file — losing it means losing access to the vault.",
            font=FONT_SM, text_color=TEXT2, wraplength=450, justify="left",
        ).pack(anchor="w", pady=(0, 10))

        key = _load_key()
        self._row(body, "Key file",  str(_key_path()))
        self._row(body, "Database",  str(DB_PATH))
        self._row(body, "API key",   key, copyable=True)

        # ── Chrome extension ──────────────────────────────────────────────────
        ctk.CTkFrame(body, height=1, fg_color=BORDER).pack(fill="x", pady=(14, 10))

        ctk.CTkLabel(body, text="Chrome Extension", font=FONT_BOLD,
                     text_color=TEXT, anchor="w").pack(fill="x", pady=(0, 6))

        ext_path = _read_ext_path()
        if ext_path:
            self._row(body, "Folder", ext_path, open_folder=True)

        ctk.CTkLabel(body, text="How to load in Chrome:",
                     font=FONT_SM, text_color=TEXT2, anchor="w").pack(fill="x", pady=(8, 4))

        for i, step in enumerate([
            "Open Chrome and go to   chrome://extensions",
            "Enable  Developer mode  using the toggle in the top-right corner",
            "Click  Load unpacked  and select the extension folder",
        ], 1):
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=str(i), width=22, font=FONT_BOLD,
                         text_color=ACCENT, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=step, font=FONT_SM, text_color=TEXT,
                         anchor="w", wraplength=420, justify="left").pack(side="left")

        # ── Startup ───────────────────────────────────────────────────────────
        ctk.CTkFrame(body, height=1, fg_color=BORDER).pack(fill="x", pady=(14, 10))
        ctk.CTkLabel(body, text="Startup", font=FONT_BOLD,
                     text_color=TEXT, anchor="w").pack(fill="x", pady=(0, 6))

        cfg = _load_config()
        self._tray_var = tk.BooleanVar(value=bool(cfg.get("start_in_tray", False)))
        ctk.CTkCheckBox(
            body, text="Start minimized to system tray",
            variable=self._tray_var,
            font=FONT_SM, text_color=TEXT2,
            fg_color=ACCENT, hover_color=ACCENT_HV,
            border_color=BORDER, checkmark_color="#ffffff",
            checkbox_width=18, checkbox_height=18, corner_radius=4,
            command=self._toggle_tray,
        ).pack(anchor="w", pady=(0, 4))

        # ── Autofill hotkey ───────────────────────────────────────────────────
        ctk.CTkFrame(body, height=1, fg_color=BORDER).pack(fill="x", pady=(14, 10))
        ctk.CTkLabel(body, text="Autofill Hotkey", font=FONT_BOLD,
                     text_color=TEXT, anchor="w").pack(fill="x", pady=(0, 6))

        hk_row = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=8)
        hk_row.pack(fill="x", pady=3)
        hk_inner = ctk.CTkFrame(hk_row, fg_color="transparent")
        hk_inner.pack(fill="x", padx=12, pady=7)
        ctk.CTkLabel(
            hk_inner, text=_get_hotkey_label(),
            font=("Consolas", 12), text_color=TEXT, anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            hk_inner, text="Change", width=70, height=28, font=FONT_SM,
            fg_color=ACCENT, hover_color=ACCENT_HV,
            command=self._change_hotkey,
        ).pack(side="left", padx=(8, 0))

        ctk.CTkButton(
            body, text="Close", width=90, height=36, font=FONT,
            fg_color=ACCENT, hover_color=ACCENT_HV,
            command=self.destroy,
        ).pack(pady=(12, 0))

    def _toggle_tray(self) -> None:
        cfg = _load_config()
        cfg["start_in_tray"] = self._tray_var.get()
        _save_config(cfg)

    def _change_hotkey(self) -> None:
        self.destroy()
        self._app._open_hotkey_dialog()

    def _row(self, parent, label: str, value: str,
             copyable: bool = False, open_folder: bool = False) -> None:
        row = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=8)
        row.pack(fill="x", pady=3)
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=7)

        ctk.CTkLabel(inner, text=label, font=FONT_SM, text_color=TEXT2,
                     width=80, anchor="w").pack(side="left")

        e = ctk.CTkEntry(inner, font=("Consolas", 10), height=28,
                         fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT)
        e.insert(0, value)
        e.configure(state="readonly")
        e.pack(side="left", fill="x", expand=True)

        if copyable:
            ctk.CTkButton(
                inner, text="Copy", width=54, height=28, font=FONT_SM,
                fg_color=ACCENT, hover_color=ACCENT_HV,
                command=lambda: (
                    self.clipboard_clear(),
                    self.clipboard_append(value),
                    self.update(),
                ),
            ).pack(side="left", padx=(8, 0))

        if open_folder:
            ctk.CTkButton(
                inner, text="Show", width=54, height=28, font=FONT_SM,
                fg_color="transparent", border_width=1, border_color=BORDER,
                hover_color=SURFACE_HV, text_color=TEXT2,
                command=lambda: subprocess.Popen(f'explorer /select,"{value}"', shell=True),
            ).pack(side="left", padx=(8, 0))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    App().mainloop()
