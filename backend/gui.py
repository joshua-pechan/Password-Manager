"""Desktop GUI for the password manager — PyQt6 edition."""
from __future__ import annotations

import csv
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
import tempfile
import threading
import time
import urllib.request
import winreg

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from PyQt6.QtCore import (Qt, QTimer, QPoint, QRect, QObject,
                           pyqtSignal, QMimeData)
from PyQt6.QtGui import (QIcon, QPixmap, QImage, QAction, QCursor, QDrag)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QLineEdit, QVBoxLayout, QHBoxLayout, QScrollArea, QCheckBox, QComboBox,
    QSlider, QProgressBar, QDialog, QMessageBox, QFileDialog,
    QSystemTrayIcon, QMenu, QSizePolicy, QAbstractScrollArea,
)

from vault import Vault, VaultError
from device_key import load as _load_key, path as _key_path, exists as _key_exists
from database import DB_PATH
import autofill as _autofill
from theme import _C, apply_dark_theme as _apply_dark_theme

logging.getLogger("werkzeug").setLevel(logging.ERROR)

try:
    from version import __version__ as _APP_VERSION
except ImportError:
    _APP_VERSION = "0.0.0"

_UPDATE_REPO   = "joshua-pechan/Password-Manager"
_UPDATE_ASSET  = "PasswordManager_Setup.exe"

from single_instance import acquire as _acquire_lock, send_show_signal as _send_show, start_ipc_listener as _start_ipc
if not _acquire_lock("gui"):
    _send_show()
    sys.exit(0)

CLIP_TTL = 30

# ── Helpers ────────────────────────────────────────────────────────────────────

def _read_ext_path() -> str:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PasswordManager")
        val = winreg.QueryValueEx(key, "ExtensionPath")[0]
        winreg.CloseKey(key)
        return val
    except Exception:
        return ""

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
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return ''.join(chars)

def _password_strength(pwd: str) -> tuple[int, str]:
    if not pwd:
        return 0, ''
    score = 0
    if len(pwd) >= 12: score += 1
    if len(pwd) >= 16: score += 1
    if len(pwd) >= 20: score += 1
    if re.search(r'[A-Z]',        pwd): score += 1
    if re.search(r'[a-z]',        pwd): score += 1
    if re.search(r'[0-9]',        pwd): score += 1
    if re.search(r'[^A-Za-z0-9]', pwd): score += 1
    if score <= 2: return 20,  'Weak'
    if score <= 4: return 50,  'Fair'
    if score <= 5: return 75,  'Strong'
    return              100, 'Very Strong'

def _make_tray_icon():
    from PIL import Image, ImageDraw
    sz = 64
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    d.ellipse([1, 1, sz-2, sz-2], fill=(31, 83, 141, 255))
    bw, bh = int(sz*0.44), int(sz*0.32)
    bx = (sz-bw)//2
    by = int(sz*0.50)
    d.rounded_rectangle([bx, by, bx+bw, by+bh], radius=3, fill=(255,255,255,255))
    sw = int(sz*0.28)
    sx = (sz-sw)//2
    arc_h = int(sz*0.38)
    sy = by - arc_h // 2
    d.arc([sx, sy, sx+sw, sy+arc_h], start=180, end=360,
          fill=(255,255,255,255), width=max(3, sz//11))
    return img

def _app_icon_pixmap() -> QPixmap | None:
    try:
        from PIL import Image
        if getattr(sys, "frozen", False):
            icon_src = pathlib.Path(sys._MEIPASS) / "icon128.png"
        else:
            icon_src = (
                pathlib.Path(__file__).parent.parent
                / "extension" / "icons" / "icon128.png"
            )
        if not icon_src.exists():
            return None
        img = Image.open(str(icon_src)).convert("RGBA")
        data = img.tobytes("raw", "RGBA")
        qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimg)
    except Exception:
        return None


# ── Cross-thread scheduler ─────────────────────────────────────────────────────

class _Scheduler(QObject):
    _sig = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._queue: list = []
        self._sig.connect(self._drain, Qt.ConnectionType.QueuedConnection)

    def schedule(self, fn):
        with self._lock:
            self._queue.append(fn)
        self._sig.emit()

    def _drain(self):
        with self._lock:
            fns, self._queue = list(self._queue), []
        for f in fns:
            try:
                f()
            except Exception:
                pass


# ── Phone export server ────────────────────────────────────────────────────────

class _PhoneExportServer:
    def __init__(self, vault, token: str, port: int,
                 cert_path: pathlib.Path, key_path: pathlib.Path) -> None:
        self.used       = threading.Event()
        self._vault     = vault
        self._token     = token
        self._port      = port
        self._cert_path = cert_path
        self._key_path  = key_path
        self._server    = None
        threading.Thread(target=self._run, daemon=True, name="phone-export").start()

    def _run(self) -> None:
        vault = self._vault; token = self._token; used = self.used

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                from urllib.parse import urlparse as _up
                if _up(self.path).path != f"/export/{token}":
                    self.send_response(404); self.end_headers(); return
                creds = vault.get_all()
                buf   = io.StringIO()
                w     = csv.writer(buf)
                w.writerow(["Title", "URL", "Username", "Password", "Notes",
                            "Type", "Group", "Tab"])
                for c in creds:
                    title     = c.get("app_name") or c["domain"]
                    cred_type = c.get("cred_type", "web")
                    type_compat = cred_type if cred_type in ("web", "app") else "web"
                    w.writerow([title, c["url"], c["username"], c["password"], "",
                                type_compat, c.get("group_name", ""), cred_type])
                data = buf.getvalue().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type",        "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="passwords.csv"')
                self.send_header("Content-Length",      str(len(data)))
                self.end_headers(); self.wfile.write(data); self.wfile.flush()
                used.set()
            def log_message(self, *_): pass

        try:
            import ssl
            srv = http.server.HTTPServer(("0.0.0.0", self._port), Handler)
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(str(self._cert_path), str(self._key_path))
            srv.socket = ssl_ctx.wrap_socket(srv.socket, server_side=True)
            srv.timeout = 1
            self._server = srv
            deadline = time.monotonic() + 65
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


# ── Phone import server ────────────────────────────────────────────────────────

def _import_csv_from_text(vault, csv_text: str) -> int:
    """Parse phone CSV and merge new credentials into vault. Returns inserted count."""
    existing = {(c["domain"], c["username"].lower()) for c in vault.get_all()}

    reader = csv.reader(io.StringIO(csv_text))
    rows   = list(reader)
    if not rows:
        return 0
    if rows[0] and rows[0][0].lower() in ("title", "name"):
        rows = rows[1:]

    count = 0
    for row in rows:
        if len(row) < 4:
            continue
        title    = row[0].strip()
        url      = row[1].strip()
        username = row[2].strip()
        password = row[3].strip()
        # row[4] = notes (ignored)
        cred_type = row[5].strip().lower() if len(row) > 5 else ""
        group     = row[6].strip()         if len(row) > 6 else ""
        tab       = row[7].strip()         if len(row) > 7 else ""

        if not username or not password:
            continue

        effective_type = tab if (tab and tab not in ("web", "app")) else (cred_type or "web")

        # Determine dedup domain key
        if effective_type == "web" and url:
            try:
                from vault import _extract_domain
                domain_key = _extract_domain(url)
            except Exception:
                domain_key = title.lower().strip()
        else:
            domain_key = (title or username).lower().strip()

        if (domain_key, username.lower()) in existing:
            continue  # already synced

        try:
            if effective_type == "web" and url:
                vault.save(url, username, password, group)
            elif effective_type == "app":
                vault.save_app(title or username, username, password, group)
            else:
                try:
                    vault.create_custom_tab(effective_type)
                except Exception:
                    pass
                vault.save_to_tab(title or username, username, password, effective_type, group)
            existing.add((domain_key, username.lower()))
            count += 1
        except Exception:
            pass

    return count


class _PhoneImportServer:
    def __init__(self, vault, token: str, port: int,
                 cert_path: pathlib.Path, key_path: pathlib.Path) -> None:
        self.received  = threading.Event()
        self.count     = 0
        self.error: str | None = None
        self._vault    = vault
        self._token    = token
        self._port     = port
        self._cert_path = cert_path
        self._key_path  = key_path
        self._server    = None
        threading.Thread(target=self._run, daemon=True, name="phone-import").start()

    def _run(self) -> None:
        vault = self._vault; token = self._token; server_ref = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                from urllib.parse import urlparse as _up
                if _up(self.path).path != f"/import/{token}":
                    self.send_response(404); self.end_headers(); return
                try:
                    length   = int(self.headers.get("Content-Length", 0))
                    csv_data = self.rfile.read(length).decode("utf-8")
                    count    = _import_csv_from_text(vault, csv_data)
                    server_ref.count = count
                    resp = json.dumps({"ok": True, "count": count}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type",   "application/json")
                    self.send_header("Content-Length", str(len(resp)))
                    self.end_headers(); self.wfile.write(resp)
                except Exception as exc:
                    server_ref.error = str(exc)
                    resp = json.dumps({"ok": False, "error": str(exc)}).encode()
                    self.send_response(500)
                    self.send_header("Content-Type",   "application/json")
                    self.send_header("Content-Length", str(len(resp)))
                    self.end_headers(); self.wfile.write(resp)
                finally:
                    server_ref.received.set()
            def log_message(self, *_): pass

        try:
            import ssl
            srv = http.server.HTTPServer(("0.0.0.0", self._port), Handler)
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(str(self._cert_path), str(self._key_path))
            srv.socket  = ssl_ctx.wrap_socket(srv.socket, server_side=True)
            srv.timeout = 1
            self._server = srv
            deadline = time.monotonic() + 120
            while not self.received.is_set() and time.monotonic() < deadline:
                srv.handle_request()
        finally:
            if self._server:
                self._server.server_close()

    def shutdown(self) -> None:
        self.received.set()
        try:
            if self._server:
                self._server.server_close()
        except Exception:
            pass


# ── Shared UI helpers ──────────────────────────────────────────────────────────

def _btn(text: str, parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    return btn

def _copy_to_clipboard(text: str) -> None:
    QApplication.clipboard().setText(text)

def _confirm(parent, title: str, msg: str) -> bool:
    dlg = QMessageBox(parent)
    dlg.setWindowTitle(title)
    dlg.setText(msg)
    dlg.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    dlg.setDefaultButton(QMessageBox.StandardButton.No)
    dlg.setIcon(QMessageBox.Icon.Warning)
    return dlg.exec() == QMessageBox.StandardButton.Yes

def _error(parent, title: str, msg: str) -> None:
    dlg = QMessageBox(parent)
    dlg.setWindowTitle(title)
    dlg.setText(msg)
    dlg.setIcon(QMessageBox.Icon.Critical)
    dlg.exec()

def _info_msg(parent, title: str, msg: str) -> None:
    dlg = QMessageBox(parent)
    dlg.setWindowTitle(title)
    dlg.setText(msg)
    dlg.setIcon(QMessageBox.Icon.Information)
    dlg.exec()

def _separator(parent=None, vertical=False) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.VLine if vertical else QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line

def _get_local_ip() -> str:
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

def _get_local_hostname() -> str:
    return socket.gethostname().split(".")[0] + ".local"

def _cert_fingerprint(cert_path: pathlib.Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return cert.fingerprint(hashes.SHA256()).hex()

def _ensure_export_cert() -> tuple[pathlib.Path, pathlib.Path, str]:
    """Returns (cert_path, key_path, hostname) — generates once, reuses thereafter."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime

    cert_dir  = pathlib.Path.home() / ".password_manager"
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "export_cert.pem"
    key_path  = cert_dir / "export_key.pem"
    hostname  = _get_local_hostname()

    if cert_path.exists() and key_path.exists():
        return cert_path, key_path, hostname

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    return cert_path, key_path, hostname

def _is_newer_version(remote: str, local: str) -> bool:
    def _parse(v: str):
        try:
            return tuple(int(x) for x in v.strip().split("."))
        except Exception:
            return (0,)
    return _parse(remote) > _parse(local)


# ── Credential card ────────────────────────────────────────────────────────────

class CredentialCard(QFrame):
    def __init__(self, parent, cred: dict, on_copy, on_edit, on_delete,
                 on_select=None, on_drag=None, avatar_variant: int = 0):
        super().__init__(parent)
        self._cred       = cred
        self._on_copy    = on_copy
        self._on_edit    = on_edit
        self._on_delete  = on_delete
        self._on_select  = on_select
        self._on_drag    = on_drag
        self._copy_btn: QPushButton | None = None
        self._selected        = False
        self._drag_start:     QPoint | None = None
        self._pending_deselect = False
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("credential_card")
        self._build()

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 11, 14, 11)
        outer.setSpacing(14)

        label = self._cred.get("app_name") or self._cred["domain"]

        info = QVBoxLayout()
        info.setSpacing(2)
        info.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel(label)
        name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        user_lbl = QLabel(self._cred["username"])
        user_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        info.addWidget(name_lbl)
        info.addWidget(user_lbl)
        outer.addLayout(info, 1)

        acts = QHBoxLayout()
        acts.setSpacing(5)
        acts.setContentsMargins(0, 0, 0, 0)

        self._copy_btn = _btn("Copy")
        self._copy_btn.clicked.connect(self._do_copy)
        acts.addWidget(self._copy_btn)

        edit_btn = _btn("Edit")
        edit_btn.clicked.connect(lambda: self._on_edit(self._cred))
        acts.addWidget(edit_btn)

        del_btn = _btn("Delete")
        del_btn.clicked.connect(lambda: self._on_delete(self._cred))
        acts.addWidget(del_btn)

        outer.addLayout(acts)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        if selected:
            self.setStyleSheet(
                f"QFrame#credential_card {{ background-color: {_C.hover}; }}"
                f" QFrame#credential_card QLabel {{ color: {_C.white}; }}"
                f" QFrame#credential_card QPushButton {{ color: {_C.white}; }}"
            )
        else:
            self.setStyleSheet("")
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self._selected:
            self.update()

    def _do_copy(self) -> None:
        self._on_copy(self._cred, self)

    def flash_copied(self) -> None:
        if self._copy_btn:
            self._copy_btn.setText("Copied")
            QTimer.singleShot(1600, self._revert_copy)

    def _revert_copy(self) -> None:
        if self._copy_btn:
            self._copy_btn.setText("Copy")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
            has_mod = bool(event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
            ))
            if self._selected and not has_mod:
                # Defer deselection: user may be about to drag the whole selection.
                # Apply it on release only if no drag occurred.
                self._pending_deselect = True
            else:
                self._pending_deselect = False
                if self._on_select:
                    self._on_select(self, event.modifiers())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._pending_deselect:
            self._pending_deselect = False
            if self._on_select:
                self._on_select(self, event.modifiers())
        self._pending_deselect = False
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (self._drag_start is not None
                and event.buttons() & Qt.MouseButton.LeftButton
                and (event.pos() - self._drag_start).manhattanLength() > 8):
            self._drag_start = None
            self._pending_deselect = False  # drag wins; keep the full selection
            if self._on_drag:
                self._on_drag(self)
        super().mouseMoveEvent(event)


# ── Credential group ───────────────────────────────────────────────────────────

class CredentialGroup(QFrame):
    def __init__(self, parent, display_name: str, group_key: str, creds: list,
                 collapsed: bool, on_toggle, on_copy, on_edit, on_delete,
                 avatar_variant: int = 0, on_register_drop=None,
                 is_manual: bool = False, on_delete_group=None,
                 on_drop=None):
        super().__init__(parent)
        self._display      = display_name
        self._key          = group_key
        self._creds        = creds
        self._collapsed    = collapsed
        self._on_toggle    = on_toggle
        self._on_copy      = on_copy
        self._on_edit      = on_edit
        self._on_delete    = on_delete
        self._on_reg_drop  = on_register_drop
        self._is_manual    = is_manual
        self._on_del_group = on_delete_group
        self._on_drop      = on_drop
        self._body_built   = False
        self._body_widget: QWidget | None = None
        self._sep_widget:  QFrame  | None = None
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("credential_group")
        self.setAcceptDrops(True)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._build_header()
        if not self._collapsed:
            self._show_body()
        if self._on_reg_drop:
            QTimer.singleShot(150, lambda: self._on_reg_drop(self._key, self))

    def _build_header(self) -> None:
        hdr = QWidget()
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(14, 10, 14, 10)
        hdr_layout.setSpacing(14)

        info = QVBoxLayout()
        info.setSpacing(2)
        info.setContentsMargins(0, 0, 0, 0)
        info.addWidget(QLabel(self._display))
        n = len(self._creds)
        count_text = "Empty — drag a login here" if n == 0 else f"{n} login{'s' if n != 1 else ''}"
        info.addWidget(QLabel(count_text))
        hdr_layout.addLayout(info, 1)

        if self._is_manual and self._on_del_group:
            db = _btn("Delete Group")
            db.clicked.connect(lambda: self._on_del_group(self._key))
            hdr_layout.addWidget(db)

        self._chevron = _btn("▲" if not self._collapsed else "▼")
        self._chevron.clicked.connect(self._toggle)
        hdr_layout.addWidget(self._chevron)

        hdr.setObjectName("group_header")
        hdr.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._outer.addWidget(hdr)
        self._hdr_widget = hdr
        hdr.mousePressEvent = lambda e: self._toggle()

    def _toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._chevron.setText("▼" if self._collapsed else "▲")
        self._on_toggle(self._key, self._collapsed)
        if self._collapsed:
            if self._sep_widget:
                self._sep_widget.hide()
            if self._body_widget:
                self._body_widget.hide()
        else:
            self._show_body()

    def _show_body(self) -> None:
        if not self._body_built:
            self._sep_widget = _separator(self)
            self._outer.addWidget(self._sep_widget)
            self._body_widget = QWidget()
            self._body_layout = QVBoxLayout(self._body_widget)
            self._body_layout.setContentsMargins(0, 0, 0, 0)
            self._body_layout.setSpacing(0)
            self._build_body()
            self._outer.addWidget(self._body_widget)
            self._body_built = True
        else:
            if self._sep_widget:
                self._sep_widget.show()
            if self._body_widget:
                self._body_widget.show()

    def _build_body(self) -> None:
        if not self._creds:
            lbl = QLabel("Drag a credential here to add it to this group")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setContentsMargins(0, 16, 0, 16)
            self._body_layout.addWidget(lbl)
            return
        for i, cred in enumerate(self._creds):
            row = QWidget()
            row.setProperty("drag_cred", cred)
            row._drag_start = None

            def _make_press(r):
                def _press(e):
                    if e.button() == Qt.MouseButton.LeftButton:
                        r._drag_start = e.pos()
                return _press

            def _make_move(r, c):
                def _move(e):
                    if (r._drag_start is not None
                            and e.buttons() & Qt.MouseButton.LeftButton
                            and (e.pos() - r._drag_start).manhattanLength() > 8):
                        r._drag_start = None
                        # Use _list_widget (self.parent()) as QDrag parent so Qt
                        # can't delete the QDrag via the short-lived row widget
                        # if processEvents() fires inside drag.exec().
                        drag = QDrag(self.parent() or self)
                        mime = QMimeData()
                        mime.setData("application/x-credential-ids",
                                     json.dumps([c["id"]]).encode("utf-8"))
                        drag.setMimeData(mime)
                        drag.exec(Qt.DropAction.MoveAction)
                        drag.deleteLater()
                return _move

            row.mousePressEvent = _make_press(row)
            row.mouseMoveEvent  = _make_move(row, cred)

            rl = QHBoxLayout(row)
            rl.setContentsMargins(14, 6 if i > 0 else 10, 14, 6)
            rl.setSpacing(10)
            label = cred.get("app_name") or cred["domain"]
            info_lbl = QLabel(f"{label}  –  {cred['username']}")
            info_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            rl.addWidget(info_lbl, 1)

            acts = QHBoxLayout()
            acts.setSpacing(5)
            acts.setContentsMargins(0, 0, 0, 0)
            copy_btn = _btn("Copy")

            def _make_copy(c, b):
                def _do():
                    self._on_copy(c, None)
                    b.setText("Copied")
                    QTimer.singleShot(1600, lambda: b.setText("Copy"))
                return _do

            copy_btn.clicked.connect(_make_copy(cred, copy_btn))
            acts.addWidget(copy_btn)

            edit_btn = _btn("Edit")
            edit_btn.clicked.connect(lambda _, c=cred: self._on_edit(c))
            acts.addWidget(edit_btn)

            del_btn = _btn("Delete")
            del_btn.clicked.connect(lambda _, c=cred: self._on_delete(c))
            acts.addWidget(del_btn)

            rl.addLayout(acts)
            self._body_layout.addWidget(row)
        self._body_layout.addSpacing(4)

    def get_drop_rect(self) -> QRect:
        return self._hdr_widget.rect().translated(
            self._hdr_widget.mapToGlobal(QPoint(0, 0))
        )

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-credential-ids"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-credential-ids"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-credential-ids") and self._on_drop:
            data = bytes(event.mimeData().data("application/x-credential-ids"))
            cred_ids = json.loads(data.decode("utf-8"))
            self._on_drop(self._key, cred_ids)
            event.acceptProposedAction()
        else:
            event.ignore()


# ── Tab button ─────────────────────────────────────────────────────────────────

class TabButton(QWidget):
    clicked = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, key: str, label: str, is_active: bool,
                 is_custom: bool, parent=None):
        super().__init__(parent)
        self._key       = key
        self._active    = is_active
        self._is_custom = is_custom
        self.setFixedHeight(44)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._drag_start_pos: QPoint | None = None
        self._build(label)

    def _build(self, label: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(6)

        self._lbl = QLabel(label)
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._lbl, 1)

        if self._is_custom:
            x_btn = QPushButton("×")
            x_btn.setFixedSize(18, 18)
            x_btn.setFlat(True)
            x_btn.setStyleSheet("padding: 0;")
            x_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            x_btn.clicked.connect(lambda: self.remove_requested.emit(self._key))
            layout.addWidget(x_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
        self._drag_start_pos = None

    def mouseMoveEvent(self, event):
        if (self._drag_start_pos is not None and
                event.buttons() & Qt.MouseButton.LeftButton):
            if (event.pos() - self._drag_start_pos).manhattanLength() > 6:
                drag = QDrag(self)
                mime = QMimeData()
                mime.setText(self._key)
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.MoveAction)
                self._drag_start_pos = None


# ── Drop-target list widget ────────────────────────────────────────────────────

class _DropTarget(QWidget):
    """Main list widget that accepts drops to remove credentials from groups."""

    def __init__(self, on_drop, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._on_drop = on_drop

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-credential-ids"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-credential-ids"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-credential-ids"):
            data = bytes(event.mimeData().data("application/x-credential-ids"))
            cred_ids = json.loads(data.decode("utf-8"))
            self._on_drop(cred_ids)
            event.acceptProposedAction()
        else:
            event.ignore()


# ── Main window ────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    _show_picker_sig = pyqtSignal(list, int)

    def __init__(self) -> None:
        super().__init__()
        self.vault      = Vault()
        self._tray      = None
        self._clip_timer: QTimer | None = None
        self._scheduler = _Scheduler()
        self.setWindowTitle("Password Manager")
        self.resize(920, 660)
        self.setMinimumSize(680, 460)
        icon = _app_icon_pixmap()
        if icon:
            self.setWindowIcon(QIcon(icon))
            QApplication.instance().setWindowIcon(QIcon(icon))
        self._show_picker_sig.connect(self._on_show_picker)
        _start_ipc(lambda: self._scheduler.schedule(self._do_restore))
        self._auto_unlock()

    def _auto_unlock(self) -> None:
        if not _key_exists() and self.vault.is_initialized():
            _error(self, "Vault Inaccessible",
                   f"Device key missing:\n{_key_path()}\n\n"
                   f"Delete the database to start fresh:\n{DB_PATH}")
            QTimer.singleShot(0, self.close)
            return

        vault_existed = self.vault.is_initialized()
        try:
            key = _load_key()
        except OSError as exc:
            if "CryptUnprotectData" not in str(exc):
                _error(self, "Startup Error", str(exc))
                QTimer.singleShot(0, self.close)
                return
            if not vault_existed:
                # Key encrypted under the wrong DPAPI context, no vault data to lose.
                # Delete it so load() regenerates it cleanly under the current user.
                _key_path().unlink(missing_ok=True)
                try:
                    key = _load_key()
                except Exception as exc2:
                    _error(self, "Startup Error", str(exc2))
                    QTimer.singleShot(0, self.close)
                    return
            else:
                _error(self, "Device Key Error",
                       "The device key could not be decrypted.\n\n"
                       "This can happen after reinstalling Windows or if the vault\n"
                       "was created under a different user account.\n\n"
                       "Restore your vault from a JSON backup, or to start fresh delete:\n"
                       f"  {_key_path()}\n  {DB_PATH}")
                QTimer.singleShot(0, self.close)
                return
        except Exception as exc:
            _error(self, "Startup Error", str(exc))
            QTimer.singleShot(0, self.close)
            return

        try:
            if not vault_existed:
                self.vault.setup(key)
            else:
                self.vault.unlock(key)
        except Exception as exc:
            _error(self, "Startup Error", str(exc))
            QTimer.singleShot(0, self.close)
            return

        self._start_server()
        self._main = MainWidget(self)
        self.setCentralWidget(self._main)
        self._start_autofill()
        if _load_config().get("start_in_tray"):
            QTimer.singleShot(300, self._on_close)
        QTimer.singleShot(5000, self._check_for_update)

    def _start_autofill(self) -> None:
        cfg = _load_config()
        mods_cfg = cfg.get("hotkey_modifiers", ["ctrl", "shift"])
        key_cfg  = cfg.get("hotkey_key", "f")
        try:
            modifiers, vk = _autofill.modifiers_vk_from_config(mods_cfg, key_cfg)
        except Exception:
            modifiers, vk = _autofill.MOD_CONTROL | _autofill.MOD_SHIFT, _autofill.VK_F
        _autofill.start(
            vault_getter=lambda: self.vault,
            show_picker=lambda creds, hwnd: self._show_picker_sig.emit(creds, hwnd),
            modifiers=modifiers,
            vk=vk,
            schedule_fn=self._scheduler.schedule,
        )

    def _on_show_picker(self, creds: list, hwnd: int) -> None:
        AppFillDialog(self, creds, hwnd).exec()

    def _open_hotkey_dialog(self) -> None:
        cfg = _load_config()
        mods_cfg = cfg.get("hotkey_modifiers", ["ctrl", "shift"])
        key_cfg  = cfg.get("hotkey_key", "f")
        try:
            mod_flags, vk = _autofill.modifiers_vk_from_config(mods_cfg, key_cfg)
        except Exception:
            mod_flags, vk = _autofill.MOD_CONTROL | _autofill.MOD_SHIFT, _autofill.VK_F
        current_label = _autofill.hotkey_label(mod_flags, vk)
        _autofill.stop()

        def _on_save(new_mod_flags, new_vk, _lbl):
            mod_names = []
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

        def _on_closed():
            _autofill.wait_stopped()
            cfg3 = _load_config()
            m3   = cfg3.get("hotkey_modifiers", mods_cfg)
            k3   = cfg3.get("hotkey_key", key_cfg)
            try:
                mf3, v3 = _autofill.modifiers_vk_from_config(m3, k3)
            except Exception:
                mf3, v3 = _autofill.MOD_CONTROL | _autofill.MOD_SHIFT, _autofill.VK_F
            _autofill.start(
                vault_getter=lambda: self.vault,
                show_picker=lambda creds, hwnd: self._show_picker_sig.emit(creds, hwnd),
                modifiers=mf3, vk=v3,
                schedule_fn=self._scheduler.schedule,
            )

        dlg = HotkeyDialog(self, current_label, _on_save)
        dlg.finished.connect(lambda _: _on_closed())
        dlg.exec()

    def _start_server(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", 7412)) == 0:
                return
        import server as srv
        threading.Thread(target=srv.run, daemon=True, name="api").start()

    def closeEvent(self, event) -> None:
        event.ignore()
        self._on_close()

    def _on_close(self) -> None:
        self.hide()
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
            QApplication.instance().quit()

    def _restore_window(self, icon=None, item=None) -> None:
        self._scheduler.schedule(self._do_restore)

    def _do_restore(self) -> None:
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.show()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self, icon=None, item=None) -> None:
        if self._tray:
            self._tray.stop()
            self._tray = None
        _autofill.stop()
        self._scheduler.schedule(lambda: QApplication.instance().quit())

    def copy_password(self, cred: dict, card=None) -> None:
        if self._clip_timer:
            self._clip_timer.stop()
        _copy_to_clipboard(cred["password"])
        if card is not None:
            card.flash_copied()
        self._clip_timer = QTimer(self)
        self._clip_timer.setSingleShot(True)
        self._clip_timer.timeout.connect(lambda: QApplication.clipboard().clear())
        self._clip_timer.start(CLIP_TTL * 1000)

    def _check_for_update(self) -> None:
        threading.Thread(target=self._update_worker, daemon=True,
                         name="update-check").start()

    def _update_worker(self) -> None:
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{_UPDATE_REPO}/releases/latest",
                headers={"User-Agent": "PasswordManager-UpdateCheck"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            tag = data.get("tag_name", "").lstrip("v")
            if not tag or not _is_newer_version(tag, _APP_VERSION):
                return
            assets = data.get("assets", [])
            dl_url = next(
                (a["browser_download_url"] for a in assets
                 if a["name"] == _UPDATE_ASSET),
                None,
            )
            if dl_url:
                self._scheduler.schedule(
                    lambda t=tag, u=dl_url: self._on_update_available(t, u)
                )
        except Exception:
            pass

    def _on_update_available(self, tag: str, url: str) -> None:
        UpdateDialog(self, tag, url).show()


# ── Main widget ────────────────────────────────────────────────────────────────

class MainWidget(QWidget):
    BUILTIN_LABELS = {"web": "Websites", "app": "Apps"}

    def __init__(self, win: MainWindow) -> None:
        super().__init__(win)
        self._win          = win
        self._active_tab   = "web"
        self._tab_order:   list[str] = []
        self._custom_tabs: list[str] = []
        self._collapsed:   dict[str, bool] = {}
        self._group_keys:  list[str] = []
        self._drop_zones:  list      = []
        self._tab_btns:    dict[str, TabButton] = {}
        self._drag_label:  QWidget | None = None
        self._drag_cred:   dict | None = None
        self._drag_active  = False
        self._drag_press_xy: QPoint | None = None
        self._db_hash      = ""
        self._selected_cards: list = []
        self._all_cards:      list = []
        self._anchor_card          = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._build_header(outer)
        self._build_search(outer)
        self._build_tabs(outer)
        self._build_list(outer)

        self._db_hash = self._get_db_hash()
        self._refresh()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_db)
        self._poll_timer.start(2000)

    # ── Header ─────────────────────────────────────────────────────────────────

    def _build_header(self, outer: QVBoxLayout) -> None:
        hdr = QWidget()
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(20, 8, 16, 8)
        hl.setSpacing(4)

        hl.addWidget(QLabel("Password Manager"))
        hl.addStretch(1)

        self._count_lbl = QLabel("")
        hl.addWidget(self._count_lbl)

        add_btn = _btn("+ Add")
        add_btn.clicked.connect(self._open_add)
        hl.addWidget(add_btn)

        grp_btn = _btn("+ Group")
        grp_btn.clicked.connect(self._open_new_group)
        hl.addWidget(grp_btn)

        gen_btn = _btn("Generator")
        gen_btn.clicked.connect(self._open_generator)
        hl.addWidget(gen_btn)

        self._transfer_btn = _btn("Transfer")
        self._transfer_btn.clicked.connect(self._show_transfer_menu)
        hl.addWidget(self._transfer_btn)

        info_btn = _btn("Info")
        info_btn.clicked.connect(self._show_info)
        hl.addWidget(info_btn)

        outer.addWidget(hdr)
        outer.addWidget(_separator())

    # ── Search ──────────────────────────────────────────────────────────────────

    def _build_search(self, outer: QVBoxLayout) -> None:
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 8, 16, 8)
        bl.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by site, app, username, or URL…")
        self._search.textChanged.connect(lambda _: self._refresh())
        bl.addWidget(self._search, 1)

        self._collapse_btn = _btn("Expand All")
        self._collapse_btn.clicked.connect(self._toggle_all_groups)
        bl.addWidget(self._collapse_btn)

        outer.addWidget(bar)
        outer.addWidget(_separator())

    # ── Tab bar ─────────────────────────────────────────────────────────────────

    def _build_tabs(self, outer: QVBoxLayout) -> None:
        tab_container = QWidget()
        tab_container.setFixedHeight(44)
        tcl = QHBoxLayout(tab_container)
        tcl.setContentsMargins(0, 0, 6, 0)
        tcl.setSpacing(0)

        scroll = QScrollArea()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(44)

        self._tab_inner = QWidget()
        self._tab_row = QHBoxLayout(self._tab_inner)
        self._tab_row.setContentsMargins(0, 0, 0, 0)
        self._tab_row.setSpacing(0)
        scroll.setWidget(self._tab_inner)
        tcl.addWidget(scroll, 1)

        add_tab_btn = QPushButton("+")
        add_tab_btn.setFixedSize(34, 34)
        add_tab_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        add_tab_btn.clicked.connect(self._open_add_tab)
        tcl.addWidget(add_tab_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addWidget(tab_container)
        outer.addWidget(_separator())
        self._rebuild_tab_row()

    def _rebuild_tab_row(self) -> None:
        while self._tab_row.count():
            item = self._tab_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._tab_btns.clear()

        try:
            self._custom_tabs = self._win.vault.get_custom_tabs()
        except Exception:
            self._custom_tabs = []

        try:
            self._tab_order = self._win.vault.get_tab_order()
        except Exception:
            self._tab_order = ["web", "app"] + list(self._custom_tabs)

        for key in self._tab_order:
            label     = self.BUILTIN_LABELS.get(key, key)
            is_active = key == self._active_tab
            is_custom = key not in self.BUILTIN_LABELS
            btn = TabButton(key, label, is_active, is_custom, self._tab_inner)
            btn.setMinimumWidth(80)
            btn.clicked.connect(self._switch_tab)
            btn.remove_requested.connect(self._remove_tab)
            self._tab_row.addWidget(btn)
            self._tab_btns[key] = btn
        self._tab_row.addStretch(1)

    def _switch_tab(self, key: str) -> None:
        self._active_tab = key
        for k, btn in self._tab_btns.items():
            btn.set_active(k == key)
        self._refresh()

    # ── Scroll list ─────────────────────────────────────────────────────────────

    def _build_list(self, outer: QVBoxLayout) -> None:
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_widget = _DropTarget(self._remove_from_group)
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(14, 8, 14, 14)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch(1)

        self._scroll_area.setWidget(self._list_widget)
        outer.addWidget(self._scroll_area, 1)

        self._empty_widget = self._make_empty_widget()

    def _make_empty_widget(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel("🔑")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(icon_lbl)

        self._empty_title = QLabel("No passwords saved yet")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(self._empty_title)

        self._empty_sub = QLabel('Click  "+ Add"  to save your first login')
        self._empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(self._empty_sub)
        return w

    # ── Refresh ──────────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        self._group_keys = []
        self._drop_zones = []
        self._selected_cards = []
        self._all_cards = []
        self._anchor_card = None
        query = self._search.text().strip().lower()

        try:
            all_creds = self._win.vault.get_all()
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

        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w and w is not self._empty_widget:
                w.deleteLater()
        if self._empty_widget.parent():
            self._empty_widget.setParent(None)

        active = [c for c in creds if c.get("cred_type", "web") == self._active_tab]

        if not active:
            if query:
                self._empty_title.setText(f'No results for "{query}"')
                self._empty_sub.setText("Try a different search term")
            elif self._active_tab == "app":
                self._empty_title.setText("No app logins saved yet")
                self._empty_sub.setText('Click  "+ Add"  →  select  "App"  to add one')
            elif self._active_tab == "web":
                self._empty_title.setText("No website logins saved yet")
                self._empty_sub.setText('Click  "+ Add"  to save your first login')
            else:
                self._empty_title.setText(f'No "{self._active_tab}" logins saved yet')
                self._empty_sub.setText('Click  "+ Add"  to add one')
            self._empty_widget.setParent(self._list_widget)
            self._list_layout.insertWidget(0, self._empty_widget)
            self._empty_widget.show()
            self._count_lbl.setText(f"{total} saved" if total else "")
            self._update_collapse_btn()
            return

        if query:
            for i, cred in enumerate(active):
                card = CredentialCard(
                    self._list_widget, cred,
                    on_copy=self._win.copy_password,
                    on_edit=self._open_edit,
                    on_delete=self._do_delete,
                    on_select=self._on_card_select,
                    on_drag=self._on_card_drag,
                )
                self._list_layout.insertWidget(i, card)
                self._all_cards.append(card)
            self._count_lbl.setText(f"{len(active)} of {total}")
            self._update_collapse_btn()
            return

        try:
            standalone: set[str] = set(self._win.vault.get_groups(self._active_tab))
        except Exception:
            standalone = set()

        grouped: dict[str, list] = {name: [] for name in standalone}
        for c in active:
            key = c.get("group_name") or c["domain"]
            grouped.setdefault(key, []).append(c)

        use_app_name = self._active_tab != "web"
        insert_pos = 0

        for key, group in sorted(grouped.items(), key=lambda x: x[0].lower()):
            is_manual = key in standalone or (group and bool(group[0].get("group_name")))
            if use_app_name:
                display = key if is_manual else ((group[0].get("app_name") or key) if group else key)
            else:
                display = key

            if len(group) == 1 and not is_manual:
                card = CredentialCard(
                    self._list_widget, group[0],
                    on_copy=self._win.copy_password,
                    on_edit=self._open_edit,
                    on_delete=self._do_delete,
                    on_select=self._on_card_select,
                    on_drag=self._on_card_drag,
                )
                self._list_layout.insertWidget(insert_pos, card)
                self._all_cards.append(card)
            else:
                self._group_keys.append(key)
                default_collapsed = len(group) > 0
                grp = CredentialGroup(
                    self._list_widget,
                    display_name=display,
                    group_key=key,
                    creds=group,
                    collapsed=self._collapsed.get(key, default_collapsed),
                    on_toggle=self._on_group_toggle,
                    on_copy=self._win.copy_password,
                    on_edit=self._open_edit,
                    on_delete=self._do_delete,
                    on_register_drop=self._register_drop_zone if is_manual else None,
                    is_manual=is_manual,
                    on_delete_group=self._delete_group if is_manual else None,
                    on_drop=self._move_to_group if is_manual else None,
                )
                self._list_layout.insertWidget(insert_pos, grp)
            insert_pos += 1

        self._count_lbl.setText(f"{len(active)} saved")
        self._update_collapse_btn()

    def _on_card_select(self, card, modifiers) -> None:
        ctrl  = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)

        if shift and self._anchor_card and self._anchor_card in self._all_cards and card in self._all_cards:
            i1 = self._all_cards.index(self._anchor_card)
            i2 = self._all_cards.index(card)
            lo, hi = min(i1, i2), max(i1, i2)
            for c in list(self._selected_cards):
                c.set_selected(False)
            self._selected_cards = []
            for c in self._all_cards[lo:hi + 1]:
                c.set_selected(True)
                self._selected_cards.append(c)
        elif ctrl:
            card.set_selected(not card._selected)
            if card._selected:
                if card not in self._selected_cards:
                    self._selected_cards.append(card)
            else:
                if card in self._selected_cards:
                    self._selected_cards.remove(card)
            self._anchor_card = card
        else:
            for c in list(self._selected_cards):
                c.set_selected(False)
            self._selected_cards = [card]
            card.set_selected(True)
            self._anchor_card = card

    def _on_card_drag(self, card) -> None:
        if not card._selected:
            for c in list(self._selected_cards):
                c.set_selected(False)
            self._selected_cards = [card]
            card.set_selected(True)
        dragging = list(self._selected_cards)
        cred_ids = [c._cred["id"] for c in dragging]
        drag = QDrag(self._list_widget)
        mime = QMimeData()
        mime.setData("application/x-credential-ids",
                     json.dumps(cred_ids).encode("utf-8"))
        drag.setMimeData(mime)
        result = drag.exec(Qt.DropAction.MoveAction)
        drag.deleteLater()
        if result != Qt.DropAction.MoveAction:
            for c in dragging:
                if c in self._all_cards:
                    c.set_selected(True)

    def _move_to_group(self, group_key: str, cred_ids: list) -> None:
        for cred_id in cred_ids:
            try:
                self._win.vault.update(cred_id, group_name=group_key)
            except Exception:
                pass
        self._selected_cards = []
        QTimer.singleShot(0, self._refresh)

    def _on_group_toggle(self, key: str, collapsed: bool) -> None:
        self._collapsed[key] = collapsed
        self._update_collapse_btn()

    def _update_collapse_btn(self) -> None:
        if not self._group_keys:
            self._collapse_btn.setText("Expand All")
            return
        all_collapsed = all(self._collapsed.get(k, True) for k in self._group_keys)
        self._collapse_btn.setText("Expand All" if all_collapsed else "Collapse All")

    def _toggle_all_groups(self) -> None:
        if not self._group_keys:
            return
        any_expanded = any(not self._collapsed.get(k, True) for k in self._group_keys)
        new_state = any_expanded
        for k in self._group_keys:
            self._collapsed[k] = new_state
        self._refresh()

    def _register_drop_zone(self, key: str, widget) -> None:
        self._drop_zones.append((key, widget))

    # ── DB watcher ───────────────────────────────────────────────────────────────

    def _get_db_hash(self) -> str:
        try:
            creds = self._win.vault.get_all()
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

    # ── CRUD ─────────────────────────────────────────────────────────────────────

    def _open_add(self) -> None:
        preset = {"web": "Website", "app": "App"}.get(self._active_tab, self._active_tab)
        dlg = CredentialDialog(
            self._win, on_save=self._on_add,
            existing_groups=self._get_existing_groups(),
            custom_tabs=self._custom_tabs,
            preset_type=preset,
        )
        dlg.exec()

    def _open_edit(self, cred: dict) -> None:
        dlg = CredentialDialog(
            self._win, existing=cred,
            on_save=lambda d: self._on_edit(cred["id"], d),
            existing_groups=self._get_existing_groups(),
            custom_tabs=self._custom_tabs,
        )
        dlg.exec()

    def _get_existing_groups(self) -> list[str]:
        try:
            from_creds = {c["group_name"] for c in self._win.vault.get_all()
                          if c.get("group_name")}
            from_table: set[str] = set()
            for t in ["web", "app"] + list(self._custom_tabs):
                from_table |= set(self._win.vault.get_groups(t))
            return sorted(from_creds | from_table)
        except Exception:
            return []

    def _on_add(self, data: dict) -> None:
        group = data.get("group_name", "")
        ct    = data.get("cred_type", "web")
        if ct == "app":
            self._win.vault.save_app(data["app_name"], data["username"], data["password"], group)
        elif ct == "web":
            self._win.vault.save(data["url"], data["username"], data["password"], group)
        else:
            self._win.vault.save_to_tab(data["app_name"], data["username"], data["password"], ct, group)
        self._refresh()

    def _on_edit(self, cred_id: int, data: dict) -> None:
        target = data.get("target_tab")
        source = data.get("cred_type", "web")
        new_ct = target if (target and target != source) else None
        explicit_app_name = data.get("app_name") or None
        if new_ct == "app" and not explicit_app_name:
            try:
                existing = self._win.vault.get_by_id(cred_id)
                explicit_app_name = (existing.get("app_name") or existing.get("domain") or "") or None
            except Exception:
                pass
        self._win.vault.update(
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
        if _confirm(self._win, "Delete",
                    f"Delete the saved login for  {name}?\n\nThis cannot be undone."):
            self._win.vault.delete(cred["id"])
            self._refresh()

    # ── Group management ─────────────────────────────────────────────────────────

    def _open_new_group(self) -> None:
        dlg = GroupNameDialog(self._win, on_create=self._on_create_group)
        dlg.exec()

    def _on_create_group(self, name: str) -> None:
        try:
            self._win.vault.create_group(name, self._active_tab)
        except Exception:
            pass
        self._refresh()

    def _remove_from_group(self, cred_ids: list) -> None:
        for cred_id in cred_ids:
            try:
                self._win.vault.update(cred_id, group_name="")
            except Exception:
                pass
        QTimer.singleShot(0, self._refresh)

    def _delete_group(self, group_key: str) -> None:
        if _confirm(self._win, "Delete Group",
                    f"Delete group '{group_key}'?\n\n"
                    "All credentials inside will be moved back to the main list."):
            self._win.vault.delete_group(group_key, self._active_tab)
            self._refresh()

    # ── Tab management ────────────────────────────────────────────────────────────

    def _open_add_tab(self) -> None:
        dlg = GroupNameDialog(
            self._win, on_create=self._do_add_tab,
            title="New Tab", header="New Tab",
            placeholder="e.g. PIN Codes, Wi-Fi, Notes…",
        )
        dlg.exec()

    def _do_add_tab(self, name: str) -> None:
        if name in self.BUILTIN_LABELS:
            return
        try:
            self._win.vault.create_custom_tab(name)
        except Exception:
            pass
        self._active_tab = name
        self._rebuild_tab_row()
        self._refresh()

    def _remove_tab(self, tab_name: str) -> None:
        if not _confirm(self._win, "Remove Tab",
                        f"Remove tab '{tab_name}'?\n\n"
                        "Its credentials will be moved to Websites."):
            return
        self._win.vault.delete_custom_tab(tab_name)
        if self._active_tab == tab_name:
            self._active_tab = "web"
        self._rebuild_tab_row()
        self._switch_tab(self._active_tab)

    # ── Transfer menu ─────────────────────────────────────────────────────────────

    def _show_transfer_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Import from CSV",      self._import_csv)
        menu.addAction("Export to CSV",        self._export_csv)
        menu.addSeparator()
        menu.addAction("Restore from Backup…", self._import_json)
        menu.addAction("Full Backup (JSON)…",  self._export_json)
        menu.addSeparator()
        menu.addAction("Send to Phone",        self._send_to_phone)
        menu.addAction("Receive from Phone",   self._receive_from_phone)
        pos = self._transfer_btn.mapToGlobal(
            QPoint(0, self._transfer_btn.height() + 2)
        )
        menu.exec(pos)

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._win, "Import Passwords", "",
            "CSV file (*.csv);;All files (*.*)"
        )
        if not path:
            return
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except Exception as exc:
            _error(self._win, "Import Failed", str(exc))
            return
        if not rows:
            _info_msg(self._win, "Import", "No data found in the CSV file.")
            return
        headers = list(rows[0].keys())

        def _col(*candidates) -> str | None:
            hl = {h.lower(): h for h in headers}
            for c in candidates:
                if c in hl: return hl[c]
            for c in candidates:
                for hl_key, h in hl.items():
                    if c in hl_key: return h
            return None

        col_url   = _col("url", "website", "web site", "site", "login_uri")
        col_user  = _col("username", "login", "email", "user", "login_username")
        col_pass  = _col("password", "pass", "login_password")
        col_title = _col("title", "name", "account", "label")
        col_type  = _col("type")
        col_group = _col("group", "group_name")
        col_tab   = _col("tab", "tab_name")

        if not col_user or not col_pass:
            _error(self._win, "Import Failed",
                   f"Could not find username/password columns.\n\n"
                   f"Columns found: {', '.join(headers)}")
            return

        vault = self._win.vault

        if col_tab:
            known_tabs = {"web", "app"} | set(vault.get_custom_tabs())
            for row in rows:
                t = row.get(col_tab, "").strip()
                if t and t not in known_tabs:
                    try:
                        vault.create_custom_tab(t)
                        known_tabs.add(t)
                    except Exception:
                        pass

        if col_group and col_tab:
            existing_groups: dict[str, set] = {}
            for row in rows:
                g = row.get(col_group, "").strip()
                t = row.get(col_tab, "").strip() or "web"
                if g:
                    existing_groups.setdefault(t, set()).add(g)
            for tab_name, names in existing_groups.items():
                for name in names:
                    try:
                        vault.create_group(name, tab_name)
                    except Exception:
                        pass

        imported = skipped = 0
        for row in rows:
            username   = row.get(col_user,  "").strip()
            password   = row.get(col_pass,  "").strip()
            url        = row.get(col_url,   "").strip() if col_url   else ""
            title      = row.get(col_title, "").strip() if col_title else ""
            group_name = row.get(col_group, "").strip() if col_group else ""
            raw_tab    = row.get(col_tab,   "").strip() if col_tab   else ""
            raw_type   = row.get(col_type,  "").strip().lower() if col_type else ""

            if not username or not password:
                skipped += 1
                continue

            if raw_tab:
                cred_type = raw_tab
            elif raw_type in ("web", "app"):
                cred_type = raw_type
            else:
                cred_type = "app" if (not url and title) else "web"

            try:
                if cred_type == "web":
                    if not url:
                        skipped += 1
                        continue
                    vault.save(url, username, password, group_name)
                elif cred_type == "app":
                    vault.save_app(title or username, username, password, group_name)
                else:
                    vault.save_to_tab(title or username, username, password,
                                      cred_type, group_name)
                imported += 1
            except Exception:
                skipped += 1

        self._refresh()
        skip_note = f"\nSkipped {skipped} rows (missing required fields)." if skipped else ""
        _info_msg(self._win, "Import Complete",
                  f"Imported {imported} credential{'s' if imported != 1 else ''}." + skip_note)

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self._win, "Export Passwords", "passwords.csv",
            "CSV file (*.csv)"
        )
        if not path:
            return
        try:
            creds = self._win.vault.get_all()
        except VaultError:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Title", "URL", "Username", "Password", "Notes",
                        "Type", "Group", "Tab"])
            for c in creds:
                title     = c.get("app_name") or c["domain"]
                cred_type = c.get("cred_type", "web")
                type_compat = cred_type if cred_type in ("web", "app") else "web"
                w.writerow([title, c["url"], c["username"], c["password"], "",
                             type_compat, c.get("group_name", ""), cred_type])
        _info_msg(self._win, "Export complete",
                  f"Exported {len(creds)} password{'s' if len(creds) != 1 else ''} to:\n{path}\n\n"
                  "Delete the file once done — it contains your passwords in plain text.")

    def _export_json(self) -> None:
        import datetime
        path, _ = QFileDialog.getSaveFileName(
            self._win, "Full Backup", "password_manager_backup.json",
            "JSON backup (*.json)"
        )
        if not path:
            return
        try:
            vault       = self._win.vault
            creds       = vault.get_all()
            custom_tabs = vault.get_custom_tabs()
            tab_order   = vault.get_tab_order()
            groups: list[dict] = []
            for tab in ["web", "app"] + custom_tabs:
                for name in vault.get_groups(tab):
                    groups.append({"name": name, "tab": tab})
        except VaultError:
            return

        backup = {
            "version":     1,
            "exported_at": datetime.datetime.now().isoformat(),
            "custom_tabs": custom_tabs,
            "tab_order":   tab_order,
            "groups":      groups,
            "credentials": [
                {
                    "title":    c.get("app_name") or c["domain"],
                    "url":      c["url"],
                    "app_name": c.get("app_name", ""),
                    "username": c["username"],
                    "password": c["password"],
                    "tab":      c.get("cred_type", "web"),
                    "group":    c.get("group_name", ""),
                }
                for c in creds
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(backup, f, indent=2, ensure_ascii=False)

        n_tabs = len(custom_tabs)
        n_grps = len(groups)
        _info_msg(
            self._win, "Backup complete",
            f"Backed up {len(creds)} credential{'s' if len(creds) != 1 else ''}"
            + (f", {n_tabs} custom tab{'s' if n_tabs != 1 else ''}" if n_tabs else "")
            + (f", and {n_grps} group{'s' if n_grps != 1 else ''}" if n_grps else "")
            + f" to:\n{path}\n\n"
            "Delete the file once done — it contains your passwords in plain text."
        )

    def _import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._win, "Restore from Backup", "",
            "JSON backup (*.json);;All files (*.*)"
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                backup = json.load(f)
        except Exception as exc:
            _error(self._win, "Restore Failed", str(exc))
            return

        if not isinstance(backup, dict) or backup.get("version") != 1:
            _error(self._win, "Restore Failed",
                   "This doesn't look like a valid backup file (missing version field).")
            return

        n_creds = len(backup.get("credentials", []))
        n_tabs  = len(backup.get("custom_tabs", []))
        n_grps  = len(backup.get("groups", []))

        detail = []
        if n_creds: detail.append(f"  • {n_creds} credential{'s' if n_creds != 1 else ''}")
        if n_tabs:  detail.append(f"  • {n_tabs} custom tab{'s' if n_tabs != 1 else ''}")
        if n_grps:  detail.append(f"  • {n_grps} group{'s' if n_grps != 1 else ''}")

        if not _confirm(
            self._win, "Restore from Backup",
            "This will add the following (existing data is NOT deleted):\n\n"
            + "\n".join(detail or ["  (nothing to restore)"])
            + "\n\nContinue?",
        ):
            return

        vault = self._win.vault

        for tab_name in backup.get("custom_tabs", []):
            try:
                vault.create_custom_tab(tab_name)
            except Exception:
                pass

        if backup.get("tab_order"):
            try:
                vault.set_tab_order(backup["tab_order"])
            except Exception:
                pass

        for g in backup.get("groups", []):
            try:
                vault.create_group(g["name"], g.get("tab", "web"))
            except Exception:
                pass

        imported = skipped = 0
        for c in backup.get("credentials", []):
            username = c.get("username", "").strip()
            password = c.get("password", "")
            if not username or not password:
                skipped += 1
                continue
            tab      = c.get("tab", "web")
            group    = c.get("group", "")
            url      = c.get("url", "")
            app_name = c.get("app_name", "") or c.get("title", "")
            try:
                if tab == "web":
                    if not url:
                        skipped += 1
                        continue
                    vault.save(url, username, password, group)
                elif tab == "app":
                    vault.save_app(app_name or username, username, password, group)
                else:
                    vault.save_to_tab(app_name or c.get("title", ""),
                                      username, password, tab, group)
                imported += 1
            except Exception:
                skipped += 1

        self._rebuild_tab_row()
        self._refresh()
        skip_note = (f"\nSkipped {skipped} item{'s' if skipped != 1 else ''} "
                     "(missing required fields).") if skipped else ""
        _info_msg(self._win, "Restore Complete",
                  f"Restored {imported} credential{'s' if imported != 1 else ''}."
                  + skip_note)

    def _send_to_phone(self) -> None:
        SendToPhoneDialog(self._win, self._win.vault).exec()

    def _receive_from_phone(self) -> None:
        ReceiveFromPhoneDialog(self._win, self._win.vault).exec()
        self._refresh()

    def _show_info(self) -> None:
        InfoDialog(self._win).exec()

    def _open_generator(self) -> None:
        GeneratorDialog(self._win).exec()


# ── Base dialog ────────────────────────────────────────────────────────────────

class BaseDialog(QDialog):
    def __init__(self, parent, title: str, width: int = 460, height: int = 500) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedSize(width, height)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        if parent:
            self._center_on_parent()

    def _center_on_parent(self) -> None:
        if p := self.parent():
            pg = p.frameGeometry()
            self.move(
                pg.center().x() - self.width() // 2,
                pg.center().y() - self.height() // 2,
            )

    def _header(self, text: str, height: int = 54) -> QWidget:
        hdr = QWidget()
        hdr.setFixedHeight(height)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(20, 0, 20, 0)
        hl.addWidget(QLabel(text))
        self._outer.addWidget(hdr)
        self._outer.addWidget(_separator())
        return hdr

    def _body_widget(self) -> QWidget:
        body = QWidget()
        self._outer.addWidget(body, 1)
        return body

    def _field(self, parent_layout: QVBoxLayout, placeholder: str,
               password: bool = False) -> QLineEdit:
        e = QLineEdit()
        e.setPlaceholderText(placeholder)
        e.setFixedHeight(40)
        if password:
            e.setEchoMode(QLineEdit.EchoMode.Password)
        parent_layout.addWidget(e)
        return e

    def _labeled_field(self, parent_layout: QVBoxLayout, label: str,
                        placeholder: str, password: bool = False) -> QLineEdit:
        parent_layout.addWidget(QLabel(label))
        e = self._field(parent_layout, placeholder, password)
        parent_layout.addSpacing(4)
        return e


# ── Add/Edit dialog ────────────────────────────────────────────────────────────

class CredentialDialog(BaseDialog):
    def __init__(self, parent, on_save, existing: dict | None = None,
                 existing_groups: list | None = None,
                 custom_tabs: list | None = None,
                 preset_type: str | None = None) -> None:
        h = 580 if existing else 530
        super().__init__(parent, "Edit Credential" if existing else "Add Credential",
                         460, h)
        self._on_save         = on_save
        self._existing        = existing
        self._existing_groups = existing_groups or []
        self._custom_tabs     = custom_tabs or []
        self._preset_type     = preset_type
        self._build()
        self._populate()

    def _build(self) -> None:
        self._header("Edit Credential" if self._existing else "New Credential")
        body = self._body_widget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(22, 14, 22, 14)
        bl.setSpacing(6)

        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        type_row.addWidget(QLabel("Type"))
        self._type_combo = QComboBox()
        self._type_combo.addItems(["Website", "App"] + self._custom_tabs)
        self._type_combo.setFixedHeight(36)
        self._type_combo.currentTextChanged.connect(self._set_type)
        type_row.addWidget(self._type_combo, 1)
        bl.addLayout(type_row)
        bl.addSpacing(4)

        self._id_label = QLabel("URL")
        bl.addWidget(self._id_label)

        self._url_entry = QLineEdit()
        self._url_entry.setPlaceholderText("https://github.com")
        self._url_entry.setFixedHeight(40)
        bl.addWidget(self._url_entry)

        self._app_entry = QLineEdit()
        self._app_entry.setPlaceholderText("Discord, Slack, Steam…")
        self._app_entry.setFixedHeight(40)
        self._app_entry.hide()
        bl.addWidget(self._app_entry)

        self._app_hint = QLabel(
            f"Enter the app's window title exactly (e.g. 'Discord', 'Slack').\n"
            f"{_get_hotkey_label()} will autofill when that window is active."
        )
        self._app_hint.setWordWrap(True)
        self._app_hint.hide()
        bl.addWidget(self._app_hint)
        bl.addSpacing(2)

        bl.addWidget(QLabel("Username / Email"))
        self._user_entry = QLineEdit()
        self._user_entry.setPlaceholderText("Username / Email")
        self._user_entry.setFixedHeight(40)
        bl.addWidget(self._user_entry)
        bl.addSpacing(2)

        bl.addWidget(QLabel("Password"))
        pw_row = QHBoxLayout()
        pw_row.setSpacing(6)
        self._pw_entry = QLineEdit()
        self._pw_entry.setPlaceholderText("Password")
        self._pw_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self._pw_entry.setFixedHeight(40)
        pw_row.addWidget(self._pw_entry, 1)
        self._eye_btn = _btn("Show")
        self._eye_btn.setFixedHeight(40)
        self._eye_btn.clicked.connect(self._toggle_pw)
        pw_row.addWidget(self._eye_btn)
        bl.addLayout(pw_row)

        self._err_lbl = QLabel("")
        bl.addWidget(self._err_lbl)

        if self._existing:
            ct = self._existing.get("cred_type", "web")
            current_tab = {"web": "Websites", "app": "Apps"}.get(ct, ct)
            tab_row = QHBoxLayout()
            tab_row.setSpacing(8)
            tab_row.addWidget(QLabel("Tab"))
            self._tab_combo = QComboBox()
            self._tab_combo.addItems(["Websites", "Apps"] + self._custom_tabs)
            self._tab_combo.setCurrentText(current_tab)
            self._tab_combo.setFixedHeight(40)
            tab_row.addWidget(self._tab_combo, 1)
            bl.addLayout(tab_row)
            bl.addSpacing(4)
        else:
            self._tab_combo = None

        group_row = QHBoxLayout()
        group_row.setSpacing(8)
        group_row.addWidget(QLabel("Group"))
        self._group_combo = QComboBox()
        self._group_combo.setEditable(True)
        self._group_combo.addItem("(none)")
        for g in self._existing_groups:
            self._group_combo.addItem(g)
        self._group_combo.setCurrentText("(none)")
        self._group_combo.setFixedHeight(40)
        group_row.addWidget(self._group_combo, 1)
        bl.addLayout(group_row)

        bl.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        cancel = _btn("Cancel")
        cancel.setFixedSize(120, 40)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch(1)
        save = _btn("Save")
        save.setFixedSize(120, 40)
        save.clicked.connect(self._submit)
        btn_row.addWidget(save)
        bl.addLayout(btn_row)

    def _populate(self) -> None:
        ex = self._existing
        if ex:
            ct = ex.get("cred_type", "web")
            if ct == "web":
                self._type_combo.setCurrentText("Website")
                self._set_type("Website")
                self._url_entry.setText(ex.get("url") or ex.get("app_name", ""))
            elif ct == "app":
                self._type_combo.setCurrentText("App")
                self._set_type("App")
                self._app_entry.setText(ex.get("app_name", ""))
            else:
                self._type_combo.setCurrentText(ct)
                self._set_type(ct)
                self._app_entry.setText(ex.get("app_name", ""))
            self._user_entry.setText(ex.get("username", ""))
            self._pw_entry.setText(ex.get("password", ""))
            grp = ex.get("group_name", "")
            self._group_combo.setCurrentText(grp if grp else "(none)")
            self._type_combo.setEnabled(False)
        elif self._preset_type:
            self._type_combo.setCurrentText(self._preset_type)
            self._set_type(self._preset_type)
        QTimer.singleShot(80, lambda: (
            self._app_entry.setFocus()
            if (ex and ex.get("cred_type", "web") != "web") else
            self._url_entry.setFocus()
        ))

    def _set_type(self, value: str) -> None:
        if value == "Website":
            self._id_label.setText("URL")
            self._app_entry.hide()
            self._app_hint.hide()
            self._url_entry.show()
        else:
            field_label = "App Name" if value == "App" else "Label"
            placeholder = "Discord, Slack, Steam…" if value == "App" else "e.g. Netflix, Bank…"
            self._id_label.setText(field_label)
            self._url_entry.hide()
            self._app_entry.setPlaceholderText(placeholder)
            self._app_entry.show()
            if value == "App":
                self._app_hint.show()
            else:
                self._app_hint.hide()

    def _toggle_pw(self) -> None:
        hidden = self._pw_entry.echoMode() == QLineEdit.EchoMode.Password
        self._pw_entry.setEchoMode(
            QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password
        )
        self._eye_btn.setText("Hide" if hidden else "Show")

    def _submit(self) -> None:
        ex = self._existing
        if ex:
            cred_type = ex.get("cred_type", "web")
        else:
            type_val  = self._type_combo.currentText()
            cred_type = {"Website": "web", "App": "app"}.get(type_val, type_val)

        identifier = (
            self._url_entry.text().strip()
            if cred_type == "web" else
            self._app_entry.text().strip()
        )
        user = self._user_entry.text().strip()
        pw   = self._pw_entry.text()

        if not identifier and not ex:
            msgs = {"web": "URL is required.", "app": "App name is required."}
            self._err_lbl.setText(msgs.get(cred_type, "Label is required."))
            return
        if not user:
            self._err_lbl.setText("Username is required.")
            return
        if not pw:
            self._err_lbl.setText("Password is required.")
            return

        raw_group  = self._group_combo.currentText().strip()
        group_name = "" if raw_group == "(none)" else raw_group

        if self._tab_combo is not None:
            tab_label  = self._tab_combo.currentText()
            target_tab = {"Websites": "web", "Apps": "app"}.get(tab_label, tab_label)
        else:
            target_tab = cred_type

        self._on_save({
            "cred_type":  cred_type,
            "target_tab": target_tab,
            "url":        identifier if cred_type == "web" else "",
            "app_name":   identifier if cred_type != "web" else "",
            "group_name": group_name,
            "username":   user,
            "password":   pw,
        })
        self.accept()


# ── Group / Tab name dialog ────────────────────────────────────────────────────

class GroupNameDialog(BaseDialog):
    def __init__(self, parent, on_create,
                 title: str = "New Group",
                 header: str = "New Group",
                 placeholder: str = "e.g. Gaming, Work, Social…") -> None:
        super().__init__(parent, title, 360, 230)
        self._on_create   = on_create
        self._header_text = header
        self._placeholder = placeholder
        self._build()

    def _build(self) -> None:
        self._header(self._header_text)
        body = self._body_widget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(22, 14, 22, 14)
        bl.setSpacing(6)

        label_text = "Tab Name" if "Tab" in self._header_text else "Group Name"
        bl.addWidget(QLabel(label_text))
        self._name_entry = QLineEdit()
        self._name_entry.setPlaceholderText(self._placeholder)
        self._name_entry.setFixedHeight(40)
        bl.addWidget(self._name_entry)

        self._err_lbl = QLabel("")
        bl.addWidget(self._err_lbl)
        bl.addStretch(1)

        btn_row = QHBoxLayout()
        cancel = _btn("Cancel")
        cancel.setFixedSize(100, 36)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch(1)
        create = _btn("Create")
        create.setFixedSize(100, 36)
        create.clicked.connect(self._submit)
        btn_row.addWidget(create)
        bl.addLayout(btn_row)

        QTimer.singleShot(80, self._name_entry.setFocus)
        self._name_entry.returnPressed.connect(self._submit)

    def _submit(self) -> None:
        name = self._name_entry.text().strip()
        if not name:
            self._err_lbl.setText("Name is required.")
            return
        self._on_create(name)
        self.accept()


# ── Password generator ─────────────────────────────────────────────────────────

class GeneratorDialog(BaseDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent, "Password Generator", 440, 490)
        self._build()

    def _build(self) -> None:
        self._header("Password Generator")
        body = self._body_widget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(28, 20, 28, 20)
        bl.setSpacing(10)

        out_row = QHBoxLayout()
        self._output = QLineEdit()
        self._output.setPlaceholderText("Click Generate…")
        self._output.setFixedHeight(46)
        self._output.setReadOnly(True)
        out_row.addWidget(self._output, 1)
        copy_btn = _btn("Copy")
        copy_btn.setFixedHeight(46)
        copy_btn.clicked.connect(self._copy)
        out_row.addWidget(copy_btn)
        bl.addLayout(out_row)

        self._copied_lbl = QLabel("")
        self._copied_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        bl.addWidget(self._copied_lbl)

        len_row = QHBoxLayout()
        len_row.addWidget(QLabel("Length"))
        self._len_slider = QSlider(Qt.Orientation.Horizontal)
        self._len_slider.setRange(8, 32)
        self._len_slider.setValue(20)
        self._len_slider.valueChanged.connect(lambda v: self._len_lbl.setText(str(v)))
        len_row.addWidget(self._len_slider, 1)
        self._len_lbl = QLabel("20")
        len_row.addWidget(self._len_lbl)
        bl.addLayout(len_row)

        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 12, 18, 12)
        cl.setSpacing(8)
        self._upper   = self._checkbox(cl, "Uppercase   (A–Z)")
        self._lower   = self._checkbox(cl, "Lowercase   (a–z)")
        self._digits  = self._checkbox(cl, "Numbers     (0–9)")
        self._symbols = self._checkbox(cl, "Symbols     (!@#…)")
        bl.addWidget(card)

        srow = QHBoxLayout()
        self._strength_bar = QProgressBar()
        self._strength_bar.setRange(0, 100)
        self._strength_bar.setValue(0)
        self._strength_bar.setTextVisible(False)
        self._strength_bar.setFixedHeight(6)
        srow.addWidget(self._strength_bar, 1)
        self._strength_lbl = QLabel("")
        self._strength_lbl.setFixedWidth(82)
        self._strength_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        srow.addWidget(self._strength_lbl)
        bl.addLayout(srow)

        gen_btn = _btn("Generate")
        gen_btn.setFixedHeight(44)
        gen_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        gen_btn.clicked.connect(self._generate)
        bl.addWidget(gen_btn)

    def _checkbox(self, layout: QVBoxLayout, text: str) -> QCheckBox:
        cb = QCheckBox(text)
        cb.setChecked(True)
        layout.addWidget(cb)
        return cb

    def _generate(self) -> None:
        pwd = _gen_password(
            self._len_slider.value(),
            upper=self._upper.isChecked(),
            lower=self._lower.isChecked(),
            digits=self._digits.isChecked(),
            symbols=self._symbols.isChecked(),
        )
        if not pwd:
            return
        self._output.setText(pwd)
        pct, label = _password_strength(pwd)
        self._strength_bar.setValue(pct)
        self._strength_lbl.setText(label)
        self._copied_lbl.setText("")

    def _copy(self) -> None:
        pwd = self._output.text()
        if not pwd:
            return
        _copy_to_clipboard(pwd)
        self._copied_lbl.setText("Copied!")
        QTimer.singleShot(2000, lambda: self._copied_lbl.setText(""))


# ── Autofill picker ────────────────────────────────────────────────────────────

class AppFillDialog(BaseDialog):
    def __init__(self, parent, creds: list, hwnd: int) -> None:
        app_name = creds[0].get("app_name") or creds[0]["domain"]
        h = 60 + len(creds) * 64 + 24
        super().__init__(parent, f"Autofill — {app_name}", 380, h)
        self._creds = creds
        self._hwnd  = hwnd
        self._build(app_name)

    def _build(self, app_name: str) -> None:
        self._header(f"Choose login for {app_name}", height=48)
        body = self._body_widget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(8)

        for cred in self._creds:
            row = QFrame()
            row.setFrameShape(QFrame.Shape.StyledPanel)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(12, 8, 12, 8)
            rl.setSpacing(8)
            rl.addWidget(QLabel(cred["username"]), 1)

            copy_btn = _btn("Copy")
            copy_btn.setFixedSize(62, 30)
            copy_btn.clicked.connect(lambda _, c=cred: self._do_copy(c))
            rl.addWidget(copy_btn)

            fill_btn = _btn("Fill")
            fill_btn.setFixedSize(62, 30)
            fill_btn.clicked.connect(lambda _, c=cred: self._do_fill(c))
            rl.addWidget(fill_btn)

            bl.addWidget(row)
        bl.addStretch(1)

    def _do_copy(self, cred: dict) -> None:
        _copy_to_clipboard(cred["password"])
        self.accept()

    def _do_fill(self, cred: dict) -> None:
        hwnd = self._hwnd
        username, password = cred["username"], cred["password"]
        self.accept()

        def _fill():
            time.sleep(0.3)
            _autofill.focus_hwnd(hwnd)
            time.sleep(0.1)
            _autofill.type_credentials(username, password)

        threading.Thread(target=_fill, daemon=True).start()


# ── Send to phone ──────────────────────────────────────────────────────────────

class SendToPhoneDialog(BaseDialog):
    _TIMEOUT = 60

    def __init__(self, parent, vault) -> None:
        super().__init__(parent, "Send to Phone", 320, 460)
        self._server: _PhoneExportServer | None = None
        self._seconds = self._TIMEOUT
        self._done    = False
        self._start_server(vault)
        self._build()
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(1000)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(300)

    def _start_server(self, vault) -> None:
        port = _find_free_port()
        token = secrets.token_urlsafe(16)
        cert_path, key_path, hostname = _ensure_export_cert()
        self._cert_path = cert_path
        fp = _cert_fingerprint(cert_path)
        self._url = f"https://{_get_local_ip()}:{port}/export/{token}?fp={fp}"
        self._server = _PhoneExportServer(vault, token, port, cert_path, key_path)

    def _build(self) -> None:
        self._header("Send to Phone")
        body = self._body_widget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(20, 16, 20, 16)
        bl.setSpacing(10)
        bl.setAlignment(Qt.AlignmentFlag.AlignTop)

        hint = QLabel("Make sure your phone is on the same network, then scan:")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(hint)
        bl.addSpacing(4)

        try:
            import qrcode as _qr
            from PIL import Image as PilImage
            qr = _qr.QRCode(box_size=7, border=2,
                            error_correction=_qr.constants.ERROR_CORRECT_M)
            qr.add_data(self._url)
            qr.make(fit=True)
            pil_img = qr.make_image(fill_color=(0, 0, 0),
                                    back_color=(255, 255, 255)).convert("RGB")
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            buf.seek(0)
            px = QPixmap()
            px.loadFromData(buf.read())
            px = px.scaled(
                220, 220,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation
            )
            img_lbl = QLabel()
            img_lbl.setPixmap(px)
            img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bl.addWidget(img_lbl)
        except Exception as _e:
            bl.addWidget(QLabel(f"QR error: {_e}"))

        url_e = QLineEdit(self._url)
        url_e.setReadOnly(True)
        url_e.setFixedHeight(28)
        bl.addWidget(url_e)

        self._status_lbl = QLabel(f"Expires in {self._seconds}s")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self._status_lbl)

        bl.addStretch(1)
        cancel = _btn("Cancel")
        cancel.setFixedHeight(36)
        cancel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        cancel.clicked.connect(self.reject)
        bl.addWidget(cancel)

    def _tick(self) -> None:
        if self._done:
            return
        self._seconds -= 1
        if self._seconds <= 0:
            self._finish(expired=True)
            return
        self._status_lbl.setText(f"Expires in {self._seconds}s")

    def _poll(self) -> None:
        if self._done:
            return
        if self._server and self._server.used.is_set():
            self._finish(expired=False)

    def _finish(self, *, expired: bool) -> None:
        self._done = True
        self._tick_timer.stop()
        self._poll_timer.stop()
        if not expired:
            self._status_lbl.setText("Downloaded!")
            QTimer.singleShot(1800, self.accept)
        else:
            self.reject()

    def closeEvent(self, event) -> None:
        if self._server:
            self._server.shutdown()
        super().closeEvent(event)

    def reject(self) -> None:
        if self._server:
            self._server.shutdown()
        super().reject()


# ── Receive-from-phone dialog ──────────────────────────────────────────────────

class ReceiveFromPhoneDialog(BaseDialog):
    _TIMEOUT = 120

    def __init__(self, parent, vault) -> None:
        super().__init__(parent, "Receive from Phone", 320, 480)
        self._vault   = vault
        self._server: _PhoneImportServer | None = None
        self._seconds = self._TIMEOUT
        self._done    = False
        self._start_server()
        self._build()
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(1000)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(300)

    def _start_server(self) -> None:
        port = _find_free_port()
        token = secrets.token_urlsafe(16)
        cert_path, key_path, _ = _ensure_export_cert()
        fp = _cert_fingerprint(cert_path)
        self._url = f"https://{_get_local_ip()}:{port}/import/{token}?fp={fp}"
        self._server = _PhoneImportServer(self._vault, token, port, cert_path, key_path)

    def _build(self) -> None:
        self._header("Receive from Phone")
        body = self._body_widget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(20, 16, 20, 16)
        bl.setSpacing(10)
        bl.setAlignment(Qt.AlignmentFlag.AlignTop)

        hint = QLabel("On your phone, tap the sync button and scan this QR code:")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(hint)
        bl.addSpacing(4)

        try:
            import qrcode as _qr
            from PIL import Image as PilImage
            qr = _qr.QRCode(box_size=7, border=2,
                            error_correction=_qr.constants.ERROR_CORRECT_M)
            qr.add_data(self._url)
            qr.make(fit=True)
            pil_img = qr.make_image(fill_color=(0, 0, 0),
                                    back_color=(255, 255, 255)).convert("RGB")
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            buf.seek(0)
            px = QPixmap()
            px.loadFromData(buf.read())
            px = px.scaled(220, 220,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.FastTransformation)
            img_lbl = QLabel()
            img_lbl.setPixmap(px)
            img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bl.addWidget(img_lbl)
        except Exception as _e:
            bl.addWidget(QLabel(f"QR error: {_e}"))

        url_e = QLineEdit(self._url)
        url_e.setReadOnly(True)
        url_e.setFixedHeight(28)
        bl.addWidget(url_e)

        self._status_lbl = QLabel(f"Waiting… (expires in {self._seconds}s)")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self._status_lbl)

        bl.addStretch(1)
        cancel = _btn("Cancel")
        cancel.setFixedHeight(36)
        cancel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        cancel.clicked.connect(self.reject)
        bl.addWidget(cancel)

    def _tick(self) -> None:
        if self._done:
            return
        self._seconds -= 1
        if self._seconds <= 0:
            self._finish(expired=True)
            return
        self._status_lbl.setText(f"Waiting… (expires in {self._seconds}s)")

    def _poll(self) -> None:
        if self._done or not self._server:
            return
        if self._server.received.is_set():
            self._finish(expired=False)

    def _finish(self, *, expired: bool) -> None:
        self._done = True
        self._tick_timer.stop()
        self._poll_timer.stop()
        if not expired and self._server:
            if self._server.error:
                self._status_lbl.setText(f"Error: {self._server.error}")
                QTimer.singleShot(3000, self.reject)
            else:
                n = self._server.count
                self._status_lbl.setText(
                    f"Received {n} new credential{'s' if n != 1 else ''}!"
                )
                QTimer.singleShot(2000, self.accept)
        else:
            self.reject()

    def closeEvent(self, event) -> None:
        if self._server:
            self._server.shutdown()
        super().closeEvent(event)

    def reject(self) -> None:
        if self._server:
            self._server.shutdown()
        super().reject()


# ── Hotkey dialog ──────────────────────────────────────────────────────────────

class HotkeyDialog(BaseDialog):
    _CTRL_MASK  = Qt.KeyboardModifier.ControlModifier
    _SHIFT_MASK = Qt.KeyboardModifier.ShiftModifier
    _ALT_MASK   = Qt.KeyboardModifier.AltModifier

    def __init__(self, parent, current_label: str, on_save) -> None:
        super().__init__(parent, "Change Autofill Hotkey", 420, 310)
        self._on_save  = on_save
        self._captured = None
        self._build(current_label)

    def _build(self, current_label: str) -> None:
        self._header("Change Autofill Hotkey", height=48)
        body = self._body_widget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(22, 14, 22, 14)
        bl.setSpacing(8)

        bl.addWidget(QLabel(f"Current:  {current_label}"))

        hint = QLabel(
            "Click the box below, then press the desired combination.\n"
            "Must include Ctrl, Shift, or Alt with a letter (A–Z) or F1–F12."
        )
        hint.setWordWrap(True)
        bl.addWidget(hint)

        self._capture_frame = QFrame()
        self._capture_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._capture_frame.setFixedHeight(56)
        self._capture_frame.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._capture_frame.mousePressEvent = lambda e: self._activate()
        cfl = QHBoxLayout(self._capture_frame)
        self._capture_lbl = QLabel("Click here, then press hotkey…")
        self._capture_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cfl.addWidget(self._capture_lbl)
        bl.addWidget(self._capture_frame)

        self._err_lbl = QLabel("")
        bl.addWidget(self._err_lbl)

        bl.addStretch(1)
        btn_row = QHBoxLayout()
        cancel = _btn("Cancel")
        cancel.setFixedSize(110, 38)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch(1)
        self._ok_btn = _btn("Save")
        self._ok_btn.setFixedSize(110, 38)
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self._submit)
        btn_row.addWidget(self._ok_btn)
        bl.addLayout(btn_row)

    def _activate(self) -> None:
        self._capture_lbl.setText("Listening…")
        self.setFocus()

    def keyPressEvent(self, event) -> None:
        key  = event.key()
        mods = event.modifiers()

        modifier_keys = {
            Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
            Qt.Key.Key_Meta, Qt.Key.Key_CapsLock,
        }
        if key in modifier_keys:
            return

        mod_flags = 0
        mod_parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            mod_flags |= _autofill.MOD_CONTROL; mod_parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            mod_flags |= _autofill.MOD_SHIFT;   mod_parts.append("Shift")
        if mods & Qt.KeyboardModifier.AltModifier:
            mod_flags |= _autofill.MOD_ALT;     mod_parts.append("Alt")

        if not mod_parts:
            self._err_lbl.setText("At least one modifier (Ctrl, Shift, Alt) is required.")
            return

        vk: int | None = None
        key_name = ""
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            vk = key - Qt.Key.Key_A + 0x41
            key_name = chr(vk)
        elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
            n = key - Qt.Key.Key_F1 + 1
            vk = 0x6F + n
            key_name = f"F{n}"

        if vk is None:
            self._err_lbl.setText("Use A–Z or F1–F12.")
            return

        label = "+".join(mod_parts + [key_name])
        self._captured = (mod_flags, vk, label)
        self._capture_lbl.setText(label)
        self._ok_btn.setEnabled(True)
        self._err_lbl.setText("")

    def _submit(self) -> None:
        if self._captured:
            self._on_save(*self._captured)
        self.accept()


# ── Info dialog ────────────────────────────────────────────────────────────────

class InfoDialog(BaseDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent, "Info", 500, 650)
        self._win = parent
        self._build()

    def _build(self) -> None:
        self._header("Info")
        body = self._body_widget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(20, 14, 20, 14)
        bl.setSpacing(8)

        warn = QLabel(
            "Back up the device key file — losing it means losing access to the vault."
        )
        warn.setWordWrap(True)
        bl.addWidget(warn)

        key = _load_key()
        self._info_row(bl, "Key file",  str(_key_path()))
        self._info_row(bl, "Database",  str(DB_PATH))
        self._info_row(bl, "API key",   key, copyable=True)

        bl.addWidget(_separator())
        bl.addWidget(QLabel("Chrome Extension"))

        ext_path = _read_ext_path()
        if ext_path:
            self._info_row(bl, "Folder", ext_path, open_folder=True)

        bl.addWidget(QLabel("How to load in Chrome:"))
        for i, step in enumerate([
            "Open Chrome and go to   chrome://extensions",
            "Enable  Developer mode  using the toggle in the top-right corner",
            "Click  Load unpacked  and select the extension folder",
        ], 1):
            row = QHBoxLayout()
            num = QLabel(str(i))
            num.setFixedWidth(22)
            row.addWidget(num)
            step_lbl = QLabel(step)
            step_lbl.setWordWrap(True)
            row.addWidget(step_lbl, 1)
            bl.addLayout(row)

        bl.addWidget(_separator())
        bl.addWidget(QLabel("Startup"))

        cfg = _load_config()
        self._tray_cb = QCheckBox("Start minimized to system tray")
        self._tray_cb.setChecked(bool(cfg.get("start_in_tray", False)))
        self._tray_cb.stateChanged.connect(self._toggle_tray)
        bl.addWidget(self._tray_cb)

        bl.addWidget(_separator())
        bl.addWidget(QLabel("Autofill Hotkey"))

        hk_row = QFrame()
        hk_row.setFrameShape(QFrame.Shape.StyledPanel)
        hkl = QHBoxLayout(hk_row)
        hkl.setContentsMargins(12, 7, 12, 7)
        hkl.addWidget(QLabel(_get_hotkey_label()), 1)
        change_btn = _btn("Change")
        change_btn.setFixedSize(70, 28)
        change_btn.clicked.connect(self._change_hotkey)
        hkl.addWidget(change_btn)
        bl.addWidget(hk_row)

        bl.addSpacing(12)
        close_btn = _btn("Close")
        close_btn.setFixedSize(90, 36)
        close_btn.clicked.connect(self.accept)
        bl.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignCenter)

    def _info_row(self, layout: QVBoxLayout, label: str, value: str,
                  copyable: bool = False, open_folder: bool = False) -> None:
        row = QFrame()
        row.setFrameShape(QFrame.Shape.StyledPanel)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(12, 7, 12, 7)
        rl.setSpacing(8)
        rl.addWidget(QLabel(label))
        e = QLineEdit(value)
        e.setReadOnly(True)
        e.setFixedHeight(28)
        rl.addWidget(e, 1)
        if copyable:
            cb = _btn("Copy")
            cb.setFixedSize(54, 28)
            cb.clicked.connect(lambda: _copy_to_clipboard(value))
            rl.addWidget(cb)
        if open_folder:
            sb = _btn("Show")
            sb.setFixedSize(54, 28)
            sb.clicked.connect(
                lambda: subprocess.Popen(f'explorer /select,"{value}"', shell=True)
            )
            rl.addWidget(sb)
        layout.addWidget(row)

    def _toggle_tray(self) -> None:
        cfg = _load_config()
        cfg["start_in_tray"] = self._tray_cb.isChecked()
        _save_config(cfg)

    def _change_hotkey(self) -> None:
        self.accept()
        self._win._open_hotkey_dialog()


# ── Auto-update dialog ─────────────────────────────────────────────────────────

class UpdateDialog(BaseDialog):
    _progress_sig = pyqtSignal(int)
    _done_sig     = pyqtSignal(str)
    _error_sig    = pyqtSignal(str)

    def __init__(self, parent, tag: str, download_url: str) -> None:
        super().__init__(parent, "Update Available", 440, 250)
        self._tag = tag
        self._url = download_url
        self._build()
        self._progress_sig.connect(self._bar.setValue)
        self._done_sig.connect(self._on_done)
        self._error_sig.connect(self._on_error)

    def _build(self) -> None:
        self._header("Update Available")
        body = self._body_widget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(22, 16, 22, 16)
        bl.setSpacing(8)

        bl.addWidget(QLabel(f"Version  {self._tag}  is available."))
        hint = QLabel(
            "The installer will download and launch automatically.\n"
            "The app will close so the update can be applied."
        )
        hint.setWordWrap(True)
        bl.addWidget(hint)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.hide()
        bl.addWidget(self._bar)

        self._status_lbl = QLabel("")
        bl.addWidget(self._status_lbl)

        bl.addStretch(1)

        row = QHBoxLayout()
        self._skip_btn = _btn("Skip")
        self._skip_btn.setFixedSize(100, 36)
        self._skip_btn.clicked.connect(self.reject)
        row.addWidget(self._skip_btn)
        row.addStretch(1)
        self._dl_btn = _btn("Download & Install")
        self._dl_btn.setFixedSize(170, 36)
        self._dl_btn.clicked.connect(self._start_download)
        row.addWidget(self._dl_btn)
        bl.addLayout(row)

    def _start_download(self) -> None:
        self._dl_btn.setEnabled(False)
        self._skip_btn.setEnabled(False)
        self._bar.setValue(0)
        self._bar.show()
        self._status_lbl.setText("Downloading…")
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self) -> None:
        try:
            tmp = tempfile.mktemp(suffix=".exe", prefix="PMSetup-")

            def _hook(count, block, total):
                if total > 0:
                    self._progress_sig.emit(min(100, int(count * block * 100 / total)))

            urllib.request.urlretrieve(self._url, tmp, _hook)
            self._done_sig.emit(tmp)
        except Exception as exc:
            self._error_sig.emit(str(exc))

    def _on_done(self, path: str) -> None:
        self._status_lbl.setText("Launching installer…")
        subprocess.Popen([path])
        QTimer.singleShot(600, lambda: QApplication.instance().quit())

    def _on_error(self, msg: str) -> None:
        self._status_lbl.setText(f"Download failed: {msg}")
        self._dl_btn.setEnabled(True)
        self._skip_btn.setEnabled(True)
        self._bar.hide()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Password Manager")
    _apply_dark_theme(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

