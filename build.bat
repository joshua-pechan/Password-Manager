@echo off
:: Build the Password Manager — produces dist/PasswordManager_Setup.exe
:: Requires: pip install pyinstaller pillow  (run once before first build)

python "%~dp0build.py"
pause
