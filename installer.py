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

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Design tokens ─────────────────────────────────────────────────────────────
BG        = "#111827"
HEADER_BG = "#0a0f1a"
SURFACE   = "#1f2937"
BORDER    = "#374151"
INPUT_BG  = "#1a2436"
ACCENT    = "#3b82f6"
ACCENT_HV = "#2563eb"
TEXT      = "#f9fafb"
TEXT2     = "#9ca3af"
GREEN     = "#4ade80"
DANGER    = "#ef4444"
DANGER_HV = "#dc2626"

FONT       = ("Segoe UI", 12)
FONT_BOLD  = ("Segoe UI", 13, "bold")
FONT_SM    = ("Segoe UI", 11)
FONT_TITLE = ("Segoe UI", 17, "bold")

_REG_KEY          = r"Software\PasswordManager"
_UNINSTALL_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PasswordManager"
_APP_VERSION       = "1.0"
_PUBLISHER         = "Joshua Pechan"


# ── Registry helpers ──────────────────────────────────────────────────────────

def _reg_read() -> tuple[str, str] | tuple[None, None]:
    """Return (install_dir, ext_dir) from the registry, or (None, None)."""
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
    """Kill any running PasswordManager.exe before touching its files."""
    subprocess.run(
        ["taskkill", "/f", "/im", "PasswordManager.exe"],
        capture_output=True,
    )


# ── Main window ───────────────────────────────────────────────────────────────

class InstallerApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.after(200, self._set_icon)

        existing_install, existing_ext = _reg_read()
        if existing_install and pathlib.Path(existing_install).exists():
            self.title("Password Manager — Manage")
            self.geometry("560x460")
            self._build_uninstall(existing_install, existing_ext or "")
        else:
            self.title("Password Manager — Setup")
            self.geometry("560x440")
            self._build_install()

    def _set_icon(self) -> None:
        try:
            import tempfile
            from PIL import Image
            img = Image.open(str(_bundled("extension/icons/icon128.png")))
            ico = pathlib.Path(tempfile.gettempdir()) / "pm_icon.ico"
            img.save(str(ico), format="ICO", sizes=[(32, 32), (48, 48)])
            self.iconbitmap(str(ico))
        except Exception:
            pass

    # ── Shared layout helpers ─────────────────────────────────────────────────

    def _section(self, parent, text: str) -> None:
        ctk.CTkLabel(parent, text=text, font=FONT_BOLD, text_color=TEXT,
                     anchor="w").pack(fill="x", pady=(0, 4))

    def _gap(self, parent, h: int = 12) -> None:
        ctk.CTkFrame(parent, height=h, fg_color="transparent").pack()

    def _info_row(self, parent, label: str, value: str) -> None:
        row = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=8)
        row.pack(fill="x", pady=3)
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=8)
        ctk.CTkLabel(inner, text=label, font=FONT_SM, text_color=TEXT2,
                     width=100, anchor="w").pack(side="left")
        ctk.CTkLabel(inner, text=value, font=FONT_SM, text_color=TEXT,
                     anchor="w").pack(side="left", fill="x", expand=True)

    # =========================================================================
    # INSTALL SIDE
    # =========================================================================

    def _build_install(self) -> None:
        self._install_var = tk.StringVar(
            value=str(pathlib.Path.home() / "AppData" / "Local" / "PasswordManager")
        )
        self._desktop_var = tk.BooleanVar(value=True)
        self._startup_var = tk.BooleanVar(value=True)
        self._inputs: list = []

        hdr = ctk.CTkFrame(self, height=62, fg_color=HEADER_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Password Manager  —  Setup",
                     font=FONT_TITLE, text_color=TEXT).pack(side="left", padx=20)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=20)

        self._section(body, "Install Location")
        self._path_row(body, self._install_var)

        ctk.CTkLabel(
            body,
            text="The Chrome extension will be installed inside this folder.\n"
                 "Open the app and click  Info  for instructions on loading it in Chrome.",
            font=FONT_SM, text_color=TEXT2, justify="left",
        ).pack(anchor="w", pady=(6, 0))
        self._gap(body)

        self._section(body, "Options")
        for label, var in [
            ("Create desktop shortcut",                 self._desktop_var),
            ("Launch automatically on Windows startup", self._startup_var),
        ]:
            cb = ctk.CTkCheckBox(
                body, text=label, variable=var,
                font=FONT, text_color=TEXT2,
                fg_color=ACCENT, hover_color=ACCENT_HV,
                border_color=BORDER, checkmark_color="#ffffff",
                checkbox_width=18, checkbox_height=18, corner_radius=4,
            )
            cb.pack(anchor="w", pady=3)
            self._inputs.append(cb)

        ctk.CTkFrame(body, height=1, fg_color=BORDER).pack(fill="x", pady=14)

        self._status_lbl = ctk.CTkLabel(body, text="Ready to install.",
                                         font=FONT_SM, text_color=TEXT2, anchor="w")
        self._status_lbl.pack(fill="x")
        self._bar = ctk.CTkProgressBar(body, height=6, corner_radius=3,
                                        fg_color=SURFACE, progress_color=ACCENT)
        self._bar.set(0)
        self._bar.pack(fill="x", pady=(4, 12))

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(fill="x")
        ctk.CTkButton(btns, text="Cancel", width=110, height=40, font=FONT,
                      fg_color="transparent", border_width=1, border_color=BORDER,
                      hover_color=SURFACE, text_color=TEXT2,
                      command=self.destroy).pack(side="left")
        self._btn = ctk.CTkButton(btns, text="Install  ->", width=140, height=40,
                                   font=FONT_BOLD, fg_color=ACCENT, hover_color=ACCENT_HV,
                                   command=self._install_start)
        self._btn.pack(side="right")

    def _path_row(self, parent, var: tk.StringVar) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 2))
        entry = ctk.CTkEntry(row, textvariable=var, height=38, font=FONT,
                             fg_color=INPUT_BG, border_color=BORDER, border_width=1,
                             corner_radius=8, text_color=TEXT)
        entry.pack(side="left", fill="x", expand=True)
        browse_btn = ctk.CTkButton(row, text="Browse", width=80, height=38, font=FONT_SM,
                                   fg_color="transparent", border_width=1, border_color=BORDER,
                                   hover_color=SURFACE, text_color=TEXT2,
                                   command=lambda v=var: self._browse(v))
        browse_btn.pack(side="left", padx=(6, 0))
        self._inputs.extend([entry, browse_btn])

    def _browse(self, var: tk.StringVar) -> None:
        current = pathlib.Path(var.get())
        initial = current.parent if current.parent.exists() else pathlib.Path.home()
        d = filedialog.askdirectory(parent=self, initialdir=str(initial))
        if d:
            var.set(str(pathlib.Path(d) / current.name))

    def _progress(self, msg: str, pct: float) -> None:
        self._status_lbl.configure(text=msg)
        self._bar.set(pct)
        self.update_idletasks()

    def _set_locked(self, locked: bool) -> None:
        state = "disabled" if locked else "normal"
        for w in self._inputs:
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _install_start(self) -> None:
        self._set_locked(True)
        self._btn.configure(state="disabled", text="Installing...")
        threading.Thread(target=self._install_run, daemon=True).start()

    def _install_run(self) -> None:
        try:
            install_dir = pathlib.Path(self._install_var.get().strip())
            ext_dir     = install_dir / "extension"

            self.after(0, self._progress, "Creating install directory...", 0.10)
            install_dir.mkdir(parents=True, exist_ok=True)

            self.after(0, self._progress, "Copying application...", 0.30)
            dst_exe   = install_dir / "PasswordManager.exe"
            dst_setup = install_dir / "PasswordManager_Setup.exe"
            shutil.copy2(_bundled("PasswordManager.exe"), dst_exe)
            shutil.copy2(sys.executable if getattr(sys, "frozen", False) else _bundled("dist/PasswordManager_Setup.exe"), dst_setup)

            self.after(0, self._progress, "Copying Chrome extension...", 0.55)
            if ext_dir.exists():
                shutil.rmtree(ext_dir, ignore_errors=True)
            shutil.copytree(_bundled("extension"), ext_dir)

            if self._desktop_var.get():
                self.after(0, self._progress, "Creating desktop shortcut...", 0.72)
                _make_shortcut(str(dst_exe),
                               str(pathlib.Path.home() / "Desktop" / "Password Manager.lnk"))

            if self._startup_var.get():
                self.after(0, self._progress, "Configuring startup...", 0.88)
                try:
                    _set_startup(str(dst_exe), enable=True)
                except Exception:
                    pass

            self.after(0, self._progress, "Saving install record...", 0.95)
            _reg_write(str(install_dir), str(ext_dir), str(dst_setup))

            self.after(0, self._progress, "Installation complete!", 1.00)
            self.after(0, self._install_done, dst_exe, ext_dir)

        except Exception as exc:
            self.after(0, messagebox.showerror, "Installation Failed", str(exc))
            self.after(0, self._set_locked, False)
            self.after(0, self._btn.configure, {"state": "normal", "text": "Install  ->"})

    def _install_done(self, dst_exe: pathlib.Path, ext_dir: pathlib.Path) -> None:
        launch = messagebox.askyesno(
            "Installation Complete",
            f"Password Manager installed to:\n  {dst_exe.parent}\n\n"
            f"Chrome extension is at:\n  {ext_dir}\n\n"
            "To load it in Chrome, open the app and click  Info  —\n"
            "it shows the folder path and step-by-step instructions.\n\n"
            "Launch Password Manager now?",
            icon="info",
        )
        self.destroy()
        if launch:
            subprocess.Popen([str(dst_exe)])

    # =========================================================================
    # UNINSTALL SIDE
    # =========================================================================

    def _build_uninstall(self, install_dir: str, ext_dir: str) -> None:
        self._u_install_dir = install_dir
        self._u_ext_dir     = ext_dir
        self._delete_data_var = tk.BooleanVar(value=False)

        hdr = ctk.CTkFrame(self, height=62, fg_color=HEADER_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="Password Manager  —  Manage Installation",
                     font=FONT_TITLE, text_color=TEXT).pack(side="left", padx=20)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=20)

        self._section(body, "Installed Files")
        self._info_row(body, "Application", install_dir)
        if ext_dir:
            self._info_row(body, "Extension", ext_dir)
        self._gap(body)

        self._section(body, "Options")
        ctk.CTkCheckBox(
            body,
            text="Also delete saved passwords and device key  (cannot be undone)",
            variable=self._delete_data_var,
            font=FONT, text_color=DANGER,
            fg_color=DANGER, hover_color=DANGER_HV,
            border_color=BORDER, checkmark_color="#ffffff",
            checkbox_width=18, checkbox_height=18, corner_radius=4,
        ).pack(anchor="w", pady=3)

        ctk.CTkFrame(body, height=1, fg_color=BORDER).pack(fill="x", pady=14)

        self._u_status_lbl = ctk.CTkLabel(body, text="Ready to uninstall.",
                                           font=FONT_SM, text_color=TEXT2, anchor="w")
        self._u_status_lbl.pack(fill="x")
        self._u_bar = ctk.CTkProgressBar(body, height=6, corner_radius=3,
                                          fg_color=SURFACE, progress_color=DANGER)
        self._u_bar.set(0)
        self._u_bar.pack(fill="x", pady=(4, 12))

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(fill="x")
        ctk.CTkButton(btns, text="Cancel", width=110, height=40, font=FONT,
                      fg_color="transparent", border_width=1, border_color=BORDER,
                      hover_color=SURFACE, text_color=TEXT2,
                      command=self.destroy).pack(side="left")
        self._u_btn = ctk.CTkButton(btns, text="Uninstall", width=120, height=40,
                                     font=FONT_BOLD, fg_color=DANGER, hover_color=DANGER_HV,
                                     command=self._uninstall_start)
        self._u_btn.pack(side="right")
        self._r_btn = ctk.CTkButton(btns, text="Reinstall", width=120, height=40,
                                     font=FONT_BOLD, fg_color=ACCENT, hover_color=ACCENT_HV,
                                     command=self._reinstall_start)
        self._r_btn.pack(side="right", padx=(0, 8))

    def _u_progress(self, msg: str, pct: float) -> None:
        self._u_status_lbl.configure(text=msg)
        self._u_bar.set(pct)
        self.update_idletasks()

    def _uninstall_start(self) -> None:
        if not messagebox.askyesno(
            "Confirm Uninstall",
            "Are you sure you want to uninstall Password Manager?",
            icon="warning",
        ):
            return
        self._u_btn.configure(state="disabled", text="Uninstalling...")
        self._r_btn.configure(state="disabled")
        threading.Thread(target=self._uninstall_run, daemon=True).start()

    def _uninstall_run(self) -> None:
        try:
            install_dir = pathlib.Path(self._u_install_dir)
            ext_dir     = pathlib.Path(self._u_ext_dir) if self._u_ext_dir else None

            self.after(0, self._u_progress, "Stopping application...", 0.10)
            _kill_app()

            self.after(0, self._u_progress, "Removing application files...", 0.25)
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)

            # Only remove the extension if it's still in the exact recorded location
            if ext_dir and ext_dir.exists():
                self.after(0, self._u_progress, "Removing Chrome extension...", 0.45)
                shutil.rmtree(ext_dir, ignore_errors=True)

            self.after(0, self._u_progress, "Removing desktop shortcut...", 0.58)
            shortcut = pathlib.Path.home() / "Desktop" / "Password Manager.lnk"
            shortcut.unlink(missing_ok=True)

            self.after(0, self._u_progress, "Removing startup entry...", 0.70)
            try:
                _set_startup("", enable=False)
            except Exception:
                pass

            if self._delete_data_var.get():
                self.after(0, self._u_progress, "Deleting vault data...", 0.82)
                data_dir = pathlib.Path.home() / ".password_manager"
                if data_dir.exists():
                    shutil.rmtree(data_dir, ignore_errors=True)

            self.after(0, self._u_progress, "Cleaning up registry...", 0.93)
            _reg_delete()

            self.after(0, self._u_progress, "Uninstall complete.", 1.00)
            self.after(0, self._uninstall_done)

        except Exception as exc:
            self.after(0, messagebox.showerror, "Uninstall Failed", str(exc))
            self.after(0, self._u_btn.configure, {"state": "normal", "text": "Uninstall"})
            self.after(0, self._r_btn.configure, {"state": "normal"})

    def _reinstall_start(self) -> None:
        self._u_btn.configure(state="disabled")
        self._r_btn.configure(state="disabled", text="Reinstalling...")
        threading.Thread(target=self._reinstall_run, daemon=True).start()

    def _reinstall_run(self) -> None:
        try:
            install_dir = pathlib.Path(self._u_install_dir)
            ext_dir     = pathlib.Path(self._u_ext_dir) if self._u_ext_dir else None

            self.after(0, self._u_progress, "Stopping application...", 0.15)
            _kill_app()

            self.after(0, self._u_progress, "Removing previous installation...", 0.40)
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)
            if ext_dir and ext_dir.exists():
                shutil.rmtree(ext_dir, ignore_errors=True)

            self.after(0, self._u_progress, "Removing shortcuts and startup...", 0.65)
            shortcut = pathlib.Path.home() / "Desktop" / "Password Manager.lnk"
            shortcut.unlink(missing_ok=True)
            try:
                _set_startup("", enable=False)
            except Exception:
                pass

            self.after(0, self._u_progress, "Cleaning up registry...", 0.85)
            _reg_delete()

            self.after(0, self._u_progress, "Ready to reinstall.", 1.00)
            self.after(0, self._switch_to_install)

        except Exception as exc:
            self.after(0, messagebox.showerror, "Reinstall Failed", str(exc))
            self.after(0, self._u_btn.configure, {"state": "normal"})
            self.after(0, self._r_btn.configure, {"state": "normal", "text": "Reinstall"})

    def _switch_to_install(self) -> None:
        for widget in self.winfo_children():
            widget.destroy()
        self.title("Password Manager — Setup")
        self.geometry("560x440")
        self._build_install()

    def _uninstall_done(self) -> None:
        messagebox.showinfo(
            "Uninstall Complete",
            "Password Manager has been removed from this PC.\n\n"
            + ("Your saved passwords and device key were also deleted.\n\n"
               if self._delete_data_var.get() else
               "Your saved passwords were left intact in:\n"
               f"  {pathlib.Path.home() / '.password_manager'}\n\n")
            + "If the Chrome extension is still loaded in Chrome, remove it manually:\n"
              "  chrome://extensions  →  find Password Manager  →  Remove",
        )
        self.destroy()


if __name__ == "__main__":
    InstallerApp().mainloop()
