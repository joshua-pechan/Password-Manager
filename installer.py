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
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import winreg

_REG_KEY           = r"Software\PasswordManager"
_UNINSTALL_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PasswordManager"
_APP_VERSION       = "1.0"
_PUBLISHER         = "Joshua Pechan"

_BG      = "#1e1e1e"
_SURFACE = "#2d2d2d"
_CARD    = "#252525"
_BTN     = "#3a3a3a"
_HOVER   = "#484848"
_FILL    = "#555555"
_BORDER  = "#4a4a4a"
_TEXT    = "#f0f0f0"
_MID     = "#666666"


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


# ── Main window ───────────────────────────────────────────────────────────────

class InstallerApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.resizable(False, False)
        self.root.configure(bg=_BG)
        self._queue: queue.Queue = queue.Queue()
        self._after_id: str | None = None

        self._apply_theme()
        self._set_icon()

        existing_install, existing_ext = _reg_read()
        if existing_install and pathlib.Path(existing_install).exists():
            self.root.title("Password Manager — Manage")
            self.root.geometry("560x460")
            self._build_uninstall(existing_install, existing_ext or "")
        else:
            self.root.title("Password Manager — Setup")
            self.root.geometry("560x440")
            self._build_install()

        self.root.mainloop()

    def _apply_theme(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".",               background=_BG,      foreground=_TEXT)
        style.configure("TFrame",          background=_BG)
        style.configure("TLabel",          background=_BG,      foreground=_TEXT)
        style.configure("TButton",         background=_BTN,     foreground=_TEXT,
                        borderwidth=1,     relief="flat",       padding=(8, 5))
        style.map("TButton",
                  background=[("active", _HOVER), ("disabled", _BG)],
                  foreground=[("disabled", _MID)])
        style.configure("TEntry",          fieldbackground=_SURFACE, foreground=_TEXT,
                        insertcolor=_TEXT, borderwidth=1,       relief="solid")
        style.configure("TCheckbutton",    background=_BG,      foreground=_TEXT)
        style.map("TCheckbutton",          background=[("active", _BG)])
        style.configure("TProgressbar",    troughcolor=_SURFACE, background=_FILL,
                        borderwidth=0,     thickness=6)
        style.configure("TSeparator",      background=_BORDER)
        style.configure("Header.TFrame",   background=_CARD)
        style.configure("Header.TLabel",   background=_CARD,    foreground=_TEXT,
                        font=("Segoe UI", 11))
        style.configure("Section.TLabel",  background=_BG,      foreground=_TEXT,
                        font=("Segoe UI", 9, "bold"))
        style.configure("Info.TFrame",     background=_SURFACE)
        style.configure("Info.TLabel",     background=_SURFACE, foreground=_TEXT)
        style.configure("InfoKey.TLabel",  background=_SURFACE, foreground=_MID,
                        font=("Segoe UI", 9))

    def _set_icon(self) -> None:
        try:
            import tempfile
            from PIL import Image
            img = Image.open(str(_bundled("extension/icons/icon128.png")))
            ico = pathlib.Path(tempfile.gettempdir()) / "pm_icon.ico"
            img.save(str(ico), format="ICO", sizes=[(32, 32), (48, 48)])
            self.root.iconbitmap(str(ico))
        except Exception:
            pass

    def _make_header(self, text: str) -> ttk.Frame:
        hdr = ttk.Frame(self.root, style="Header.TFrame", height=62)
        hdr.pack_propagate(False)
        ttk.Label(hdr, text=text, style="Header.TLabel").pack(side="left", padx=20)
        return hdr

    def _make_section_label(self, parent: tk.Widget, text: str) -> ttk.Label:
        return ttk.Label(parent, text=text, style="Section.TLabel")

    def _make_info_row(self, parent: tk.Widget, label: str, value: str) -> ttk.Frame:
        row = ttk.Frame(parent, style="Info.TFrame", padding=(12, 8))
        ttk.Label(row, text=label, style="InfoKey.TLabel", width=12).pack(side="left")
        ttk.Label(row, text=value, style="Info.TLabel", wraplength=380).pack(side="left", fill="x", expand=True)
        return row

    # =========================================================================
    # INSTALL SIDE
    # =========================================================================

    def _build_install(self) -> None:
        self._make_header("Password Manager  —  Setup").pack(fill="x")

        body = ttk.Frame(self.root, padding=(28, 20, 28, 20))
        body.pack(fill="both", expand=True)

        self._make_section_label(body, "Install Location").pack(anchor="w")
        ttk.Separator(body).pack(fill="x", pady=(2, 6))

        path_row = ttk.Frame(body)
        path_row.pack(fill="x")

        default = str(pathlib.Path.home() / "AppData" / "Local" / "PasswordManager")
        self._install_var = tk.StringVar(value=default)
        self._install_entry = ttk.Entry(path_row, textvariable=self._install_var, width=50)
        self._install_entry.pack(side="left", fill="x", expand=True, ipady=4)

        self._browse_btn = ttk.Button(path_row, text="Browse", width=8,
                                      command=self._browse_install)
        self._browse_btn.pack(side="left", padx=(6, 0))

        ttk.Label(body,
            text="The Chrome extension will be installed inside this folder.\n"
                 "Open the app and click  Info  for instructions on loading it in Chrome.",
            foreground=_MID, wraplength=490,
        ).pack(anchor="w", pady=(6, 12))

        self._make_section_label(body, "Options").pack(anchor="w")
        ttk.Separator(body).pack(fill="x", pady=(2, 6))

        self._desktop_var = tk.BooleanVar(value=True)
        self._startup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text="Create desktop shortcut",
                        variable=self._desktop_var).pack(anchor="w")
        ttk.Checkbutton(body, text="Launch automatically on Windows startup",
                        variable=self._startup_var).pack(anchor="w", pady=(3, 0))

        ttk.Separator(body).pack(fill="x", pady=(14, 8))

        self._install_status_var = tk.StringVar(value="Ready to install.")
        ttk.Label(body, textvariable=self._install_status_var).pack(anchor="w")

        self._install_bar = ttk.Progressbar(body, maximum=1000, value=0)
        self._install_bar.pack(fill="x", pady=(4, 12))

        btns = ttk.Frame(body)
        btns.pack(fill="x")
        ttk.Button(btns, text="Cancel", width=10, command=self.root.destroy).pack(side="left")
        self._install_btn = ttk.Button(btns, text="Install", width=12,
                                       command=self._install_start)
        self._install_btn.pack(side="right")

    def _browse_install(self) -> None:
        current = pathlib.Path(self._install_var.get())
        initial = str(current.parent) if current.parent.exists() else str(pathlib.Path.home())
        d = filedialog.askdirectory(parent=self.root, title="Select Install Folder",
                                    initialdir=initial)
        if d:
            self._install_var.set(str(pathlib.Path(d) / current.name))

    def _install_start(self) -> None:
        self._install_entry.config(state="disabled")
        self._browse_btn.config(state="disabled")
        self._install_btn.config(state="disabled", text="Installing...")
        path    = self._install_var.get().strip()
        desktop = self._desktop_var.get()
        startup = self._startup_var.get()
        self._after_id = self.root.after(50, self._poll_install_queue)
        threading.Thread(target=self._install_run, args=(path, desktop, startup),
                         daemon=True).start()

    def _poll_install_queue(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    self._install_status_var.set(item[1])
                    self._install_bar["value"] = int(item[2] * 1000)
                elif kind == "done":
                    self._install_done(item[1], item[2])
                    return
                elif kind == "error":
                    messagebox.showerror(item[1], item[2], parent=self.root)
                    self._install_entry.config(state="normal")
                    self._browse_btn.config(state="normal")
                    self._install_btn.config(state="normal", text="Install")
                    return
        except queue.Empty:
            pass
        self._after_id = self.root.after(50, self._poll_install_queue)

    def _install_run(self, path: str, desktop: bool, startup: bool) -> None:
        try:
            install_dir = pathlib.Path(path)
            ext_dir     = install_dir / "extension"

            self._queue.put(("progress", "Creating install directory...", 0.10))
            install_dir.mkdir(parents=True, exist_ok=True)

            self._queue.put(("progress", "Copying application...", 0.30))
            dst_exe   = install_dir / "PasswordManager.exe"
            dst_setup = install_dir / "PasswordManager_Setup.exe"
            shutil.copy2(_bundled("PasswordManager.exe"), dst_exe)
            src_setup = (sys.executable if getattr(sys, "frozen", False)
                         else _bundled("dist/PasswordManager_Setup.exe"))
            shutil.copy2(src_setup, dst_setup)

            self._queue.put(("progress", "Copying Chrome extension...", 0.55))
            if ext_dir.exists():
                shutil.rmtree(ext_dir, ignore_errors=True)
            shutil.copytree(_bundled("extension"), ext_dir)

            if desktop:
                self._queue.put(("progress", "Creating desktop shortcut...", 0.72))
                _make_shortcut(str(dst_exe),
                               str(pathlib.Path.home() / "Desktop" / "Password Manager.lnk"))

            if startup:
                self._queue.put(("progress", "Configuring startup...", 0.88))
                try:
                    _set_startup(str(dst_exe), enable=True)
                except Exception:
                    pass

            self._queue.put(("progress", "Saving install record...", 0.95))
            _reg_write(str(install_dir), str(ext_dir), str(dst_setup))

            self._queue.put(("progress", "Installation complete!", 1.00))
            self._queue.put(("done", str(dst_exe), str(ext_dir)))

        except Exception as exc:
            self._queue.put(("error", "Installation Failed", str(exc)))

    def _install_done(self, dst_exe: str, ext_dir: str) -> None:
        install_dir = str(pathlib.Path(dst_exe).parent)
        launch = messagebox.askyesno(
            "Installation Complete",
            f"Password Manager installed to:\n  {install_dir}\n\n"
            f"Chrome extension is at:\n  {ext_dir}\n\n"
            "To load it in Chrome, open the app and click  Info  —\n"
            "it shows the folder path and step-by-step instructions.\n\n"
            "Launch Password Manager now?",
            parent=self.root,
        )
        self.root.destroy()
        if launch:
            subprocess.Popen([dst_exe])

    # =========================================================================
    # UNINSTALL SIDE
    # =========================================================================

    def _build_uninstall(self, install_dir: str, ext_dir: str) -> None:
        self._u_install_dir = install_dir
        self._u_ext_dir     = ext_dir

        self._make_header("Password Manager  —  Manage Installation").pack(fill="x")

        body = ttk.Frame(self.root, padding=(28, 20, 28, 20))
        body.pack(fill="both", expand=True)

        self._make_section_label(body, "Installed Files").pack(anchor="w")
        ttk.Separator(body).pack(fill="x", pady=(2, 4))
        self._make_info_row(body, "Application", install_dir).pack(fill="x", pady=(0, 3))
        if ext_dir:
            self._make_info_row(body, "Extension", ext_dir).pack(fill="x")

        ttk.Separator(body).pack(fill="x", pady=(12, 8))

        self._make_section_label(body, "Options").pack(anchor="w", pady=(0, 4))
        self._delete_data_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(body,
            text="Also delete saved passwords and device key  (cannot be undone)",
            variable=self._delete_data_var).pack(anchor="w")

        ttk.Separator(body).pack(fill="x", pady=(14, 8))

        self._u_status_var = tk.StringVar(value="Ready to uninstall.")
        ttk.Label(body, textvariable=self._u_status_var).pack(anchor="w")

        self._u_bar = ttk.Progressbar(body, maximum=1000, value=0)
        self._u_bar.pack(fill="x", pady=(4, 12))

        btns = ttk.Frame(body)
        btns.pack(fill="x")
        ttk.Button(btns, text="Cancel", width=10, command=self.root.destroy).pack(side="left")
        self._r_btn = ttk.Button(btns, text="Reinstall", width=10,
                                  command=self._reinstall_start)
        self._r_btn.pack(side="right", padx=(8, 0))
        self._u_btn = ttk.Button(btns, text="Uninstall", width=10,
                                  command=self._uninstall_start)
        self._u_btn.pack(side="right")

    def _uninstall_start(self) -> None:
        if not messagebox.askyesno("Confirm Uninstall",
                "Are you sure you want to uninstall Password Manager?",
                parent=self.root):
            return
        self._u_btn.config(state="disabled", text="Uninstalling...")
        self._r_btn.config(state="disabled")
        delete_data = self._delete_data_var.get()
        self._after_id = self.root.after(50, self._poll_uninstall_queue)
        threading.Thread(target=self._uninstall_run, args=(delete_data,), daemon=True).start()

    def _poll_uninstall_queue(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    self._u_status_var.set(item[1])
                    self._u_bar["value"] = int(item[2] * 1000)
                elif kind == "done":
                    self._uninstall_done()
                    return
                elif kind == "reinstall_done":
                    self._switch_to_install()
                    return
                elif kind == "error":
                    messagebox.showerror(item[1], item[2], parent=self.root)
                    self._u_btn.config(state="normal", text="Uninstall")
                    self._r_btn.config(state="normal", text="Reinstall")
                    return
        except queue.Empty:
            pass
        self._after_id = self.root.after(50, self._poll_uninstall_queue)

    def _uninstall_run(self, delete_data: bool) -> None:
        try:
            install_dir = pathlib.Path(self._u_install_dir)
            ext_dir     = pathlib.Path(self._u_ext_dir) if self._u_ext_dir else None

            self._queue.put(("progress", "Stopping application...", 0.10))
            _kill_app()

            self._queue.put(("progress", "Removing application files...", 0.25))
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)

            if ext_dir and ext_dir.exists():
                self._queue.put(("progress", "Removing Chrome extension...", 0.45))
                shutil.rmtree(ext_dir, ignore_errors=True)

            self._queue.put(("progress", "Removing desktop shortcut...", 0.58))
            (pathlib.Path.home() / "Desktop" / "Password Manager.lnk").unlink(missing_ok=True)

            self._queue.put(("progress", "Removing startup entry...", 0.70))
            try:
                _set_startup("", enable=False)
            except Exception:
                pass

            if delete_data:
                self._queue.put(("progress", "Deleting vault data...", 0.82))
                data_dir = pathlib.Path.home() / ".password_manager"
                if data_dir.exists():
                    shutil.rmtree(data_dir, ignore_errors=True)

            self._queue.put(("progress", "Cleaning up registry...", 0.93))
            _reg_delete()

            self._queue.put(("progress", "Uninstall complete.", 1.00))
            self._queue.put(("done",))

        except Exception as exc:
            self._queue.put(("error", "Uninstall Failed", str(exc)))

    def _uninstall_done(self) -> None:
        delete_data = self._delete_data_var.get()
        msg = (
            "Password Manager has been removed from this PC.\n\n"
            + ("Your saved passwords and device key were also deleted.\n\n"
               if delete_data else
               "Your saved passwords were left intact in:\n"
               f"  {pathlib.Path.home() / '.password_manager'}\n\n")
            + "If the Chrome extension is still loaded in Chrome, remove it manually:\n"
              "  chrome://extensions  →  find Password Manager  →  Remove"
        )
        messagebox.showinfo("Uninstall Complete", msg, parent=self.root)
        self.root.destroy()

    def _reinstall_start(self) -> None:
        self._u_btn.config(state="disabled")
        self._r_btn.config(state="disabled", text="Reinstalling...")
        self._after_id = self.root.after(50, self._poll_uninstall_queue)
        threading.Thread(target=self._reinstall_run, daemon=True).start()

    def _reinstall_run(self) -> None:
        try:
            install_dir = pathlib.Path(self._u_install_dir)
            ext_dir     = pathlib.Path(self._u_ext_dir) if self._u_ext_dir else None

            self._queue.put(("progress", "Stopping application...", 0.15))
            _kill_app()

            self._queue.put(("progress", "Removing previous installation...", 0.40))
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)
            if ext_dir and ext_dir.exists():
                shutil.rmtree(ext_dir, ignore_errors=True)

            self._queue.put(("progress", "Removing shortcuts and startup...", 0.65))
            (pathlib.Path.home() / "Desktop" / "Password Manager.lnk").unlink(missing_ok=True)
            try:
                _set_startup("", enable=False)
            except Exception:
                pass

            self._queue.put(("progress", "Cleaning up registry...", 0.85))
            _reg_delete()

            self._queue.put(("progress", "Ready to reinstall.", 1.00))
            self._queue.put(("reinstall_done",))

        except Exception as exc:
            self._queue.put(("error", "Reinstall Failed", str(exc)))

    def _switch_to_install(self) -> None:
        if self._after_id:
            self.root.after_cancel(self._after_id)
            self._after_id = None
        for widget in self.root.winfo_children():
            widget.destroy()
        self.root.title("Password Manager — Setup")
        self.root.geometry("560x440")
        self._build_install()


if __name__ == "__main__":
    InstallerApp()
