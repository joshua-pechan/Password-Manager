@echo off
:: Build the Password Manager — produces dist/PasswordManager_Setup.exe
:: On first run: creates a clean venv and installs dependencies.
:: On subsequent runs: reuses the existing venv (fast).

setlocal
set VENV=%~dp0build_venv
set PYTHON=%VENV%\Scripts\python.exe
set PIP=%VENV%\Scripts\pip.exe

if not exist "%PYTHON%" (
    echo [setup]  Creating build virtual environment...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause & exit /b 1
    )
    echo [setup]  Installing dependencies...
    "%PIP%" install --quiet PyQt6 flask cryptography pystray keyboard Pillow pyinstaller
    if errorlevel 1 (
        echo ERROR: pip install failed.
        pause & exit /b 1
    )
    echo.
)

"%PYTHON%" "%~dp0build.py"
pause
