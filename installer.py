"""
Password Manager Installer / Uninstaller
Compiled by build.py into  dist/PasswordManager_Setup.exe

On first run  →  shows the install wizard.
On re-run     →  detects the existing installation via the registry and shows
                 the uninstall screen instead.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import threading
import winreg

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel, QPushButton,
    QLineEdit, QCheckBox, QProgressBar, QHBoxLayout, QVBoxLayout,
    QFileDialog, QMessageBox,
)

_REG_KEY           = r"Software\PasswordManager"
_UNINSTALL_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PasswordManager"
_APP_VERSION       = "1.0"
_PUBLISHER         = "Joshua Pechan"


# ── Registry helpers ──────────────────────────────────────────────────────────

def _reg_read() -> tuple[str, str] | tuple[None, None]:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY)
        install = winreg.QueryValueEx(key, "InstallPath")[0]
        ext     = winreg.QueryValueEx(key, "ExtensionPath")[0]
        winreg.CloseKey(key)
        return install, ext
    except FileNotFoundError:
        return None, None


def _reg_write(install_dir: str, ext_dir: str, uninstall_exe: str) -> None:
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_KEY)
    winreg.SetValueEx(key, "InstallPath",   0, winreg.REG_SZ, install_dir)
    winreg.SetValueEx(key, "ExtensionPath", 0, winreg.REG_SZ, ext_dir)
    winreg.CloseKey(key)

    ukey = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_REG_KEY)
    winreg.SetValueEx(ukey, "DisplayName",     0, winreg.REG_SZ,   "Password Manager")
    winreg.SetValueEx(ukey, "DisplayVersion",  0, winreg.REG_SZ,   _APP_VERSION)
    winreg.SetValueEx(ukey, "Publisher",       0, winreg.REG_SZ,   _PUBLISHER)
    winreg.SetValueEx(ukey, "InstallLocation", 0, winreg.REG_SZ,   install_dir)
    winreg.SetValueEx(ukey, "UninstallString", 0, winreg.REG_SZ,   f'"{uninstall_exe}"')
    winreg.SetValueEx(ukey, "DisplayIcon",     0, winreg.REG_SZ,   f'"{install_dir}\\PasswordManager.exe"')
    winreg.SetValueEx(ukey, "NoModify",        0, winreg.REG_DWORD, 1)
    winreg.SetValueEx(ukey, "NoRepair",        0, winreg.REG_DWORD, 1)
    winreg.CloseKey(ukey)


def _reg_delete() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _REG_KEY)
    except FileNotFoundError:
        pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_REG_KEY)
    except FileNotFoundError:
        pass


# ── Shared helpers ────────────────────────────────────────────────────────────

def _bundled(rel: str) -> pathlib.Path:
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys._MEIPASS) / rel
    return pathlib.Path(__file__).parent / rel


def _make_shortcut(target: str, link: str) -> None:
    ps = (
        f'$s=(New-Object -COM WScript.Shell).CreateShortcut("{link}");'
        f'$s.TargetPath="{target}";'
        f'$s.WorkingDirectory="{os.path.dirname(target)}";'
        f'$s.Save()'
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
    )


def _set_startup(target: str, enable: bool) -> None:
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0, winreg.KEY_SET_VALUE,
    )
    if enable:
        winreg.SetValueEx(key, "PasswordManager", 0, winreg.REG_SZ, f'"{target}"')
    else:
        try:
            winreg.DeleteValue(key, "PasswordManager")
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)


def _kill_app() -> None:
    subprocess.run(["taskkill", "/f", "/im", "PasswordManager.exe"], capture_output=True)


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


# ── Main window ───────────────────────────────────────────────────────────────

class InstallerApp(QWidget):
    _progress_sig        = pyqtSignal(str, float)
    _install_done_sig    = pyqtSignal(str, str)
    _uninstall_done_sig  = pyqtSignal()
    _reinstall_done_sig  = pyqtSignal()
    _error_sig           = pyqtSignal(str, str)
    _unlock_uninstall_sig = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Window)
        self.setFixedWidth(560)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        self._set_icon()

        existing_install, existing_ext = _reg_read()
        if existing_install and pathlib.Path(existing_install).exists():
            self.setWindowTitle("Password Manager — Manage")
            self.setFixedHeight(460)
            self._build_uninstall(existing_install, existing_ext or "")
        else:
            self.setWindowTitle("Password Manager — Setup")
            self.setFixedHeight(440)
            self._build_install()

    def _set_icon(self) -> None:
        try:
            import tempfile
            from PIL import Image
            img = Image.open(str(_bundled("extension/icons/icon128.png")))
            ico = pathlib.Path(tempfile.gettempdir()) / "pm_icon.ico"
            img.save(str(ico), format="ICO", sizes=[(32, 32), (48, 48)])
            self.setWindowIcon(QIcon(str(ico)))
        except Exception:
            pass

    # ── Layout helpers ────────────────────────────────────────────────────────

    def _make_header(self, text: str) -> QFrame:
        hdr = QFrame()
        hdr.setFixedHeight(62)
        hdr.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QHBoxLayout(hdr)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.addWidget(QLabel(text))
        lay.addStretch()
        return hdr

    def _make_section_label(self, text: str) -> QLabel:
        return QLabel(text)

    def _make_info_row(self, label: str, value: str) -> QFrame:
        row = QFrame()
        row.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 8, 12, 8)
        lbl = QLabel(label)
        lbl.setFixedWidth(100)
        val = QLabel(value)
        val.setWordWrap(True)
        lay.addWidget(lbl)
        lay.addWidget(val, 1)
        return row

    # =========================================================================
    # INSTALL SIDE
    # =========================================================================

    def _build_install(self) -> None:
        self._install_inputs: list[QWidget] = []

        self._root_layout.addWidget(self._make_header("Password Manager  —  Setup"))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(0)
        self._root_layout.addWidget(body, 1)

        lay.addWidget(self._make_section_label("Install Location"))
        lay.addSpacing(4)

        path_row = QWidget()
        path_lay = QHBoxLayout(path_row)
        path_lay.setContentsMargins(0, 0, 0, 0)
        path_lay.setSpacing(6)

        default = str(pathlib.Path.home() / "AppData" / "Local" / "PasswordManager")
        self._install_entry = QLineEdit(default)
        self._install_entry.setFixedHeight(38)

        browse_btn = QPushButton("Browse")
        browse_btn.setFixedSize(80, 38)
        browse_btn.clicked.connect(self._browse_install)

        path_lay.addWidget(self._install_entry, 1)
        path_lay.addWidget(browse_btn)
        lay.addWidget(path_row)
        self._install_inputs.extend([self._install_entry, browse_btn])

        hint = QLabel(
            "The Chrome extension will be installed inside this folder.\n"
            "Open the app and click  Info  for instructions on loading it in Chrome."
        )
        hint.setWordWrap(True)
        lay.addSpacing(6)
        lay.addWidget(hint)
        lay.addSpacing(12)

        lay.addWidget(self._make_section_label("Options"))
        lay.addSpacing(4)

        self._desktop_cb = QCheckBox("Create desktop shortcut")
        self._desktop_cb.setChecked(True)
        lay.addWidget(self._desktop_cb)
        lay.addSpacing(3)

        self._startup_cb = QCheckBox("Launch automatically on Windows startup")
        self._startup_cb.setChecked(True)
        lay.addWidget(self._startup_cb)
        self._install_inputs.extend([self._desktop_cb, self._startup_cb])

        lay.addSpacing(14)
        lay.addWidget(_separator())
        lay.addSpacing(8)

        self._install_status = QLabel("Ready to install.")
        lay.addWidget(self._install_status)
        lay.addSpacing(4)

        self._install_bar = QProgressBar()
        self._install_bar.setRange(0, 1000)
        self._install_bar.setValue(0)
        self._install_bar.setFixedHeight(6)
        self._install_bar.setTextVisible(False)
        lay.addWidget(self._install_bar)
        lay.addSpacing(12)

        btns = QWidget()
        btns_lay = QHBoxLayout(btns)
        btns_lay.setContentsMargins(0, 0, 0, 0)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(110, 40)
        cancel_btn.clicked.connect(self.close)

        self._install_btn = QPushButton("Install")
        self._install_btn.setFixedSize(140, 40)
        self._install_btn.clicked.connect(self._install_start)

        btns_lay.addWidget(cancel_btn)
        btns_lay.addStretch()
        btns_lay.addWidget(self._install_btn)
        lay.addWidget(btns)

        self._progress_sig.connect(self._on_install_progress)
        self._install_done_sig.connect(self._install_done)
        self._error_sig.connect(self._on_install_error)

    def _browse_install(self) -> None:
        current = pathlib.Path(self._install_entry.text())
        initial = str(current.parent) if current.parent.exists() else str(pathlib.Path.home())
        d = QFileDialog.getExistingDirectory(self, "Select Install Folder", initial)
        if d:
            self._install_entry.setText(str(pathlib.Path(d) / current.name))

    def _on_install_progress(self, msg: str, pct: float) -> None:
        self._install_status.setText(msg)
        self._install_bar.setValue(int(pct * 1000))

    def _set_install_locked(self, locked: bool) -> None:
        for w in self._install_inputs:
            w.setEnabled(not locked)

    def _install_start(self) -> None:
        self._set_install_locked(True)
        self._install_btn.setEnabled(False)
        self._install_btn.setText("Installing...")
        path    = self._install_entry.text().strip()
        desktop = self._desktop_cb.isChecked()
        startup = self._startup_cb.isChecked()
        threading.Thread(target=self._install_run, args=(path, desktop, startup), daemon=True).start()

    def _install_run(self, path: str, desktop: bool, startup: bool) -> None:
        try:
            install_dir = pathlib.Path(path)
            ext_dir     = install_dir / "extension"

            self._progress_sig.emit("Creating install directory...", 0.10)
            install_dir.mkdir(parents=True, exist_ok=True)

            self._progress_sig.emit("Copying application...", 0.30)
            dst_exe   = install_dir / "PasswordManager.exe"
            dst_setup = install_dir / "PasswordManager_Setup.exe"
            shutil.copy2(_bundled("PasswordManager.exe"), dst_exe)
            src_setup = sys.executable if getattr(sys, "frozen", False) else _bundled("dist/PasswordManager_Setup.exe")
            shutil.copy2(src_setup, dst_setup)

            self._progress_sig.emit("Copying Chrome extension...", 0.55)
            if ext_dir.exists():
                shutil.rmtree(ext_dir, ignore_errors=True)
            shutil.copytree(_bundled("extension"), ext_dir)

            if desktop:
                self._progress_sig.emit("Creating desktop shortcut...", 0.72)
                _make_shortcut(str(dst_exe),
                               str(pathlib.Path.home() / "Desktop" / "Password Manager.lnk"))

            if startup:
                self._progress_sig.emit("Configuring startup...", 0.88)
                try:
                    _set_startup(str(dst_exe), enable=True)
                except Exception:
                    pass

            self._progress_sig.emit("Saving install record...", 0.95)
            _reg_write(str(install_dir), str(ext_dir), str(dst_setup))

            self._progress_sig.emit("Installation complete!", 1.00)
            self._install_done_sig.emit(str(dst_exe), str(ext_dir))

        except Exception as exc:
            self._error_sig.emit("Installation Failed", str(exc))

    def _install_done(self, dst_exe: str, ext_dir: str) -> None:
        install_dir = str(pathlib.Path(dst_exe).parent)
        reply = QMessageBox.question(
            self, "Installation Complete",
            f"Password Manager installed to:\n  {install_dir}\n\n"
            f"Chrome extension is at:\n  {ext_dir}\n\n"
            "To load it in Chrome, open the app and click  Info  —\n"
            "it shows the folder path and step-by-step instructions.\n\n"
            "Launch Password Manager now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        self.close()
        if reply == QMessageBox.StandardButton.Yes:
            subprocess.Popen([dst_exe])

    def _on_install_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)
        self._install_btn.setEnabled(True)
        self._install_btn.setText("Install")
        self._set_install_locked(False)

    # =========================================================================
    # UNINSTALL SIDE
    # =========================================================================

    def _build_uninstall(self, install_dir: str, ext_dir: str) -> None:
        self._u_install_dir = install_dir
        self._u_ext_dir     = ext_dir

        self._root_layout.addWidget(self._make_header("Password Manager  —  Manage Installation"))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(0)
        self._root_layout.addWidget(body, 1)

        lay.addWidget(self._make_section_label("Installed Files"))
        lay.addSpacing(4)
        lay.addWidget(self._make_info_row("Application", install_dir))
        if ext_dir:
            lay.addSpacing(3)
            lay.addWidget(self._make_info_row("Extension", ext_dir))
        lay.addSpacing(12)

        lay.addWidget(self._make_section_label("Options"))
        lay.addSpacing(4)

        self._delete_data_cb = QCheckBox("Also delete saved passwords and device key  (cannot be undone)")
        self._delete_data_cb.setChecked(False)
        lay.addWidget(self._delete_data_cb)

        lay.addSpacing(14)
        lay.addWidget(_separator())
        lay.addSpacing(8)

        self._u_status = QLabel("Ready to uninstall.")
        lay.addWidget(self._u_status)
        lay.addSpacing(4)

        self._u_bar = QProgressBar()
        self._u_bar.setRange(0, 1000)
        self._u_bar.setValue(0)
        self._u_bar.setFixedHeight(6)
        self._u_bar.setTextVisible(False)
        lay.addWidget(self._u_bar)
        lay.addSpacing(12)

        btns = QWidget()
        btns_lay = QHBoxLayout(btns)
        btns_lay.setContentsMargins(0, 0, 0, 0)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedSize(110, 40)
        cancel_btn.clicked.connect(self.close)

        self._r_btn = QPushButton("Reinstall")
        self._r_btn.setFixedSize(120, 40)
        self._r_btn.clicked.connect(self._reinstall_start)

        self._u_btn = QPushButton("Uninstall")
        self._u_btn.setFixedSize(120, 40)
        self._u_btn.clicked.connect(self._uninstall_start)

        btns_lay.addWidget(cancel_btn)
        btns_lay.addStretch()
        btns_lay.addWidget(self._r_btn)
        btns_lay.addSpacing(8)
        btns_lay.addWidget(self._u_btn)
        lay.addWidget(btns)

        self._progress_sig.connect(self._on_u_progress)
        self._uninstall_done_sig.connect(self._uninstall_done)
        self._reinstall_done_sig.connect(self._switch_to_install)
        self._error_sig.connect(self._on_u_error)
        self._unlock_uninstall_sig.connect(self._unlock_uninstall)

    def _on_u_progress(self, msg: str, pct: float) -> None:
        self._u_status.setText(msg)
        self._u_bar.setValue(int(pct * 1000))

    def _unlock_uninstall(self) -> None:
        self._u_btn.setEnabled(True)
        self._u_btn.setText("Uninstall")
        self._r_btn.setEnabled(True)
        self._r_btn.setText("Reinstall")

    def _uninstall_start(self) -> None:
        reply = QMessageBox.question(
            self, "Confirm Uninstall",
            "Are you sure you want to uninstall Password Manager?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._u_btn.setEnabled(False)
        self._u_btn.setText("Uninstalling...")
        self._r_btn.setEnabled(False)
        delete_data = self._delete_data_cb.isChecked()
        threading.Thread(target=self._uninstall_run, args=(delete_data,), daemon=True).start()

    def _uninstall_run(self, delete_data: bool) -> None:
        try:
            install_dir = pathlib.Path(self._u_install_dir)
            ext_dir     = pathlib.Path(self._u_ext_dir) if self._u_ext_dir else None

            self._progress_sig.emit("Stopping application...", 0.10)
            _kill_app()

            self._progress_sig.emit("Removing application files...", 0.25)
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)

            if ext_dir and ext_dir.exists():
                self._progress_sig.emit("Removing Chrome extension...", 0.45)
                shutil.rmtree(ext_dir, ignore_errors=True)

            self._progress_sig.emit("Removing desktop shortcut...", 0.58)
            (pathlib.Path.home() / "Desktop" / "Password Manager.lnk").unlink(missing_ok=True)

            self._progress_sig.emit("Removing startup entry...", 0.70)
            try:
                _set_startup("", enable=False)
            except Exception:
                pass

            if delete_data:
                self._progress_sig.emit("Deleting vault data...", 0.82)
                data_dir = pathlib.Path.home() / ".password_manager"
                if data_dir.exists():
                    shutil.rmtree(data_dir, ignore_errors=True)

            self._progress_sig.emit("Cleaning up registry...", 0.93)
            _reg_delete()

            self._progress_sig.emit("Uninstall complete.", 1.00)
            self._uninstall_done_sig.emit()

        except Exception as exc:
            self._error_sig.emit("Uninstall Failed", str(exc))
            self._unlock_uninstall_sig.emit()

    def _on_u_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def _uninstall_done(self) -> None:
        delete_data = self._delete_data_cb.isChecked()
        msg = (
            "Password Manager has been removed from this PC.\n\n"
            + ("Your saved passwords and device key were also deleted.\n\n"
               if delete_data else
               "Your saved passwords were left intact in:\n"
               f"  {pathlib.Path.home() / '.password_manager'}\n\n")
            + "If the Chrome extension is still loaded in Chrome, remove it manually:\n"
              "  chrome://extensions  →  find Password Manager  →  Remove"
        )
        QMessageBox.information(self, "Uninstall Complete", msg)
        self.close()

    def _reinstall_start(self) -> None:
        self._u_btn.setEnabled(False)
        self._r_btn.setEnabled(False)
        self._r_btn.setText("Reinstalling...")
        threading.Thread(target=self._reinstall_run, daemon=True).start()

    def _reinstall_run(self) -> None:
        try:
            install_dir = pathlib.Path(self._u_install_dir)
            ext_dir     = pathlib.Path(self._u_ext_dir) if self._u_ext_dir else None

            self._progress_sig.emit("Stopping application...", 0.15)
            _kill_app()

            self._progress_sig.emit("Removing previous installation...", 0.40)
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)
            if ext_dir and ext_dir.exists():
                shutil.rmtree(ext_dir, ignore_errors=True)

            self._progress_sig.emit("Removing shortcuts and startup...", 0.65)
            (pathlib.Path.home() / "Desktop" / "Password Manager.lnk").unlink(missing_ok=True)
            try:
                _set_startup("", enable=False)
            except Exception:
                pass

            self._progress_sig.emit("Cleaning up registry...", 0.85)
            _reg_delete()

            self._progress_sig.emit("Ready to reinstall.", 1.00)
            self._reinstall_done_sig.emit()

        except Exception as exc:
            self._error_sig.emit("Reinstall Failed", str(exc))
            self._unlock_uninstall_sig.emit()

    def _switch_to_install(self) -> None:
        for sig in (self._progress_sig, self._error_sig, self._uninstall_done_sig,
                    self._reinstall_done_sig, self._unlock_uninstall_sig):
            try:
                sig.disconnect()
            except Exception:
                pass

        while self._root_layout.count():
            item = self._root_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.setWindowTitle("Password Manager — Setup")
        self.setFixedHeight(440)
        self._build_install()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = InstallerApp()
    window.show()
    sys.exit(app.exec())
