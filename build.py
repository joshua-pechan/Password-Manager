"""
Build script — run this to produce  dist/PasswordManager_Setup.exe

Usage:
    cd "Password Manager"
    pip install pyinstaller
    python build.py

Output:
    dist/PasswordManager_Setup.exe   ← give this to users

What it does:
  1. Converts icon128.png → build/icon.ico
  2. Builds dist/PasswordManager.exe   (the main app, PyInstaller --onefile)
  3. Builds dist/PasswordManager_Setup.exe   (installer, embeds the EXE + extension)
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT      = pathlib.Path(__file__).parent
BACKEND   = ROOT / "backend"
SRC       = BACKEND / "src"
EXTENSION = ROOT / "extension"
DIST      = ROOT / "dist"
BUILD     = ROOT / "build"


def run(cmd: list) -> None:
    flat = [str(c) for c in cmd]
    print(f"\n  $ {' '.join(flat)}\n")
    subprocess.run(flat, check=True)


def make_ico() -> pathlib.Path:
    from PIL import Image
    BUILD.mkdir(parents=True, exist_ok=True)
    ico = BUILD / "icon.ico"
    img = Image.open(EXTENSION / "icons" / "icon128.png")
    img.save(str(ico), format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128)])
    return ico


def build_main_app(ico: pathlib.Path) -> pathlib.Path:
    """Produce dist/PasswordManager.exe (self-contained, no Python needed)."""
    run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", "PasswordManager",
        "--distpath", DIST,
        "--workpath", BUILD / "app",
        "--specpath", BUILD,
        f"--icon={ico}",
        # Bundle the lock icon so the window titlebar icon works at runtime
        f"--add-data={EXTENSION / 'icons' / 'icon128.png'}{os.pathsep}.",
        # Collect all data files for packages that need them at runtime
        "--collect-all", "pystray",
        "--exclude-module", "PyQt5",
        "--exclude-module", "customtkinter",
        # Tell PyInstaller where to find the backend source modules
        f"--paths={SRC}",
        f"--paths={BACKEND}",
        # Dynamic imports that the static analyser can miss
        "--hidden-import", "version",
        "--hidden-import", "keyboard",
        "--hidden-import", "cryptography.hazmat.backends",
        "--hidden-import", "cryptography.hazmat.primitives.kdf.pbkdf2",
        "--hidden-import", "pystray._base_icon",
        "--hidden-import", "pystray.backends.win32",
        "--hidden-import", "flask",
        "--hidden-import", "werkzeug",
        "--hidden-import", "werkzeug.serving",
        BACKEND / "gui.py",
    ])
    exe = DIST / "PasswordManager.exe"
    if not exe.exists():
        raise FileNotFoundError("PasswordManager.exe was not produced by PyInstaller")
    return exe


def build_installer(app_exe: pathlib.Path, ico: pathlib.Path) -> pathlib.Path:
    """Produce dist/PasswordManager_Setup.exe embedding the app EXE + extension."""
    # PyInstaller --add-data  src:dest  (colon on Linux/Mac, semicolon on Windows)
    sep = os.pathsep  # ";" on Windows

    run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", "PasswordManager_Setup",
        "--distpath", DIST,
        "--workpath", BUILD / "installer",
        "--specpath", BUILD,
        f"--icon={ico}",
        "--exclude-module", "PyQt5",
        "--exclude-module", "customtkinter",
        # Embed the main EXE at the root of the bundle
        f"--add-data={app_exe}{sep}.",
        # Embed the whole extension folder
        f"--add-data={EXTENSION}{sep}extension",
        ROOT / "installer.py",
    ])
    exe = DIST / "PasswordManager_Setup.exe"
    if not exe.exists():
        raise FileNotFoundError("PasswordManager_Setup.exe was not produced by PyInstaller")
    return exe


if __name__ == "__main__":
    print("=" * 46)
    print("   Password Manager  --  Build Script")
    print("=" * 46)

    # Sanity checks
    if not (BACKEND / "gui.py").exists():
        sys.exit("ERROR: backend/gui.py not found — run this script from the project root")
    if not (EXTENSION / "manifest.json").exists():
        sys.exit("ERROR: extension/manifest.json not found")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("ERROR: PyInstaller not installed.\n  Run:  pip install pyinstaller")

    print("\n[1/3]  Converting icon PNG to ICO ...")
    ico = make_ico()
    print(f"       {ico}")

    print("\n[2/3]  Building main application (PasswordManager.exe) ...")
    print("       This may take a few minutes.")
    app_exe = build_main_app(ico)
    size_mb = app_exe.stat().st_size / 1_048_576
    print(f"       {app_exe}  ({size_mb:.1f} MB)")

    print("\n[3/3]  Building installer (PasswordManager_Setup.exe) ...")
    setup_exe = build_installer(app_exe, ico)
    size_mb = setup_exe.stat().st_size / 1_048_576
    print(f"       {setup_exe}  ({size_mb:.1f} MB)")

    print("\n" + "=" * 46)
    print("   Build complete!")
    print("=" * 46)
    print(f"\n  Installer:  {setup_exe}")
    print("\n  Distribute PasswordManager_Setup.exe to install on any PC.")
    print("  No Python required on the target machine.\n")
