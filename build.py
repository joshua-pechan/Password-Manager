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

import pathlib
import shutil
import subprocess
import sys

ROOT    = pathlib.Path(__file__).parent
BACKEND = ROOT / "backend"
DIST    = ROOT / "dist"
BUILD   = ROOT / "build"


def run(cmd: list) -> None:
    flat = [str(c) for c in cmd]
    print(f"\n  $ {' '.join(flat)}\n")
    subprocess.run(flat, check=True)


def make_ico() -> pathlib.Path:
    from PIL import Image
    BUILD.mkdir(parents=True, exist_ok=True)
    ico = BUILD / "icon.ico"
    img = Image.open(ROOT / "extension" / "icons" / "icon128.png")
    img.save(str(ico), format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128)])
    return ico


def build_main_app() -> pathlib.Path:
    """Produce dist/PasswordManager.exe (self-contained, no Python needed)."""
    run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--distpath", DIST,
        "--workpath", BUILD / "app",
        ROOT / "PasswordManager.spec",
    ])
    exe = DIST / "PasswordManager.exe"
    if not exe.exists():
        raise FileNotFoundError("PasswordManager.exe was not produced by PyInstaller")
    return exe


def build_installer(app_exe: pathlib.Path) -> pathlib.Path:
    """Produce dist/PasswordManager_Setup.exe embedding the app EXE + extension."""
    run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--distpath", DIST,
        "--workpath", BUILD / "installer",
        ROOT / "PasswordManager_Setup.spec",
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
    if not (ROOT / "extension" / "manifest.json").exists():
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
    app_exe = build_main_app()
    size_mb = app_exe.stat().st_size / 1_048_576
    print(f"       {app_exe}  ({size_mb:.1f} MB)")

    print("\n[3/3]  Building installer (PasswordManager_Setup.exe) ...")
    setup_exe = build_installer(app_exe)
    size_mb = setup_exe.stat().st_size / 1_048_576
    print(f"       {setup_exe}  ({size_mb:.1f} MB)")

    print("\n" + "=" * 46)
    print("   Build complete!")
    print("=" * 46)
    print(f"\n  Installer:  {setup_exe}")
    print("\n  Distribute PasswordManager_Setup.exe to install on any PC.")
    print("  No Python required on the target machine.\n")
