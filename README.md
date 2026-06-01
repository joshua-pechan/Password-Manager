# Password Manager

A local-only password manager. No cloud, no account, no master password prompt.
All data stays on your PC. A Chrome extension handles auto-fill and credential saving in the browser.

---

## How it works

```
Chrome Extension  ──►  Local API Server (127.0.0.1:7412)  ──►  Encrypted SQLite DB
Desktop GUI       ──►  Same database, same encryption
```

- The **API server** (`backend/main.py`) runs in the background and exposes a REST API on localhost only.
- The **desktop GUI** (`backend/gui.py`) lets you view, add, edit, and delete saved logins.
- The **Chrome extension** (`extension/`) auto-fills passwords on login pages and offers to save new ones.
- Authentication uses a **device key file** — a random key generated once and stored at  
  `C:\Users\<you>\.password_manager\device.key`. No password to type.

---

## Installation

**Option 1 — Installer (recommended)**

Run `dist/PasswordManager_Setup.exe`.

**First run — Install wizard**

1. Choose an install location (default: `%LocalAppData%\PasswordManager`).
2. Optionally create a desktop shortcut and enable auto-start at Windows login.
3. Click **Install →**. The wizard copies `PasswordManager.exe`, `PasswordManager_Setup.exe`,
   and the Chrome extension into the install folder, writes registry keys, and optionally
   creates the shortcut and startup entry.
4. Click **Yes** when prompted to launch the app.

**Re-running Setup.exe — Manage Installation**

If Password Manager is already installed, running `PasswordManager_Setup.exe` again (from the
install folder or the original download) opens the **Manage Installation** screen instead.
It shows the current install location and Chrome extension path, and offers two actions:

- **Reinstall** — stops the running app, removes all installed files, clears registry entries
  and shortcuts, then opens the install wizard so you can pick a new location or options.
  Your saved passwords are never touched.
- **Uninstall** — stops the running app, removes all installed files, removes the desktop
  shortcut and startup entry, and cleans up registry keys.
  - Check **Also delete saved passwords and device key** to also wipe
    `~/.password_manager/` (vault, device key, config). **This cannot be undone.**
  - If the box is left unchecked, your vault data is kept and can be used again if you
    reinstall later.
  - After uninstall, remove the Chrome extension manually:
    `chrome://extensions` → find **Password Manager** → **Remove**.

**Option 2 — Build from source**

```
pip install pyinstaller pillow
double-click build.bat
```

This runs `build.py` and produces `dist/PasswordManager_Setup.exe`.

**Option 3 — Run directly from source (developers)**

```
cd backend
pip install -r requirements.txt
python gui.py
```

---

## Chrome extension setup

1. Open the desktop GUI → click **Info** → copy the API key.
2. Go to `chrome://extensions` → enable **Developer mode** → **Load unpacked** → select the `extension/` folder.
3. Open the extension popup → **Settings** tab → paste the API key → **Save**.

---

## Folder structure

```
Password Manager/
├── build.bat              ← double-click to build dist/PasswordManager_Setup.exe
├── build.py               ← build script (PyInstaller; produces main app + installer)
├── installer.py           ← installer/uninstaller GUI (bundled into Setup.exe)
├── backend/               ← Python server + desktop GUI
│   ├── gui.py             ← desktop app entry point
│   ├── main.py            ← server-only entry point (used by startup task)
│   ├── startup.py         ← register auto-start at Windows login (source installs)
│   ├── requirements.txt
│   └── src/               ← core Python modules
└── extension/             ← Chrome extension (load unpacked in Chrome)
```

---

## Data files (outside the project folder)

| File | Purpose |
|---|---|
| `~/.password_manager/vault.db` | Encrypted SQLite database — all saved logins |
| `~/.password_manager/device.key` | The encryption/auth key — **back this up** |
| `~/.password_manager/config.json` | GUI preferences (e.g. start-in-tray) |

> If `device.key` is lost, the vault cannot be decrypted. Back it up somewhere safe.  
> To move to a new PC, copy **both** `vault.db` and `device.key`.

---

## Transferring passwords

The **Transfer** button in the toolbar opens a menu with three options:

### Export to CSV

Saves all credentials to a plain-text CSV file. Use this to import into another password manager
or a phone browser. **Delete the file immediately after — it contains all passwords in plain text.**

### Import from CSV

Imports credentials from a CSV file. Accepts exports from Chrome, Firefox, Bitwarden, 1Password,
and the app's own export format. Columns are detected automatically (case-insensitive).

### Send to Phone

Opens a dialog showing a **QR code** and a one-time URL. Scan the QR code with your phone
(both devices must be on the same Wi-Fi network) to download the CSV directly — no cables or
cloud storage required. The link expires after 60 seconds and can only be used once.

---

## Importing passwords into phone browsers

### iPhone — Safari

1. **Transfer → Export to CSV** (or use **Send to Phone** to download it directly)
2. Transfer the CSV to your iPhone via iCloud Drive, AirDrop, or email if needed
3. On your iPhone: **Settings → Safari → Import**
4. Tap **Choose File** and select the CSV
5. Tap **Import** to confirm, then **Done**
6. Delete the CSV file after importing

### Android — Google Chrome

1. **Transfer → Export to CSV** (or use **Send to Phone** to download it to your phone)
2. On a browser (phone or PC), go to **[passwords.google.com](https://passwords.google.com)**
3. Click the **Settings** gear icon → **Import passwords** → select the CSV file
4. The passwords sync automatically to Chrome and Android's autofill on your phone
5. Delete the CSV file after importing

> **Alternatively** — in newer versions of Chrome for Android, you can import directly:  
> Chrome menu (⋮) → **Settings** → **Password Manager** → gear icon → **Import passwords**

---

## Auto-start at login (source installs)

If you're running from source (not using the installer), register the server to start automatically:

```
cd backend
python startup.py           ← registers a Windows Task Scheduler logon task
python startup.py --remove  ← removes it
```

The installer handles this automatically via the Windows registry startup key.

# Tech Stack
Backend (Python)
- PyQt6 — desktop GUI framework
- Flask — local REST API server (runs on 127.0.0.1:7412)
- SQLite — credential storage (~/.password_manager/vault.db)
- pystray — system tray icon
- Pillow (PIL) — image generation for the tray icon
- ctypes / Windows API — global hotkey registration for autofill (RegisterHotKey)
- PyInstaller — packaging into a standalone .exe (based on the build/ artifacts)

Browser Extension (JavaScript)
- Manifest V3 Chrome extension
- Vanilla JS — background.js, content.js, popup.js
- Communicates with the local Flask API over http://127.0.0.1:7412

Platform
- Windows only — uses winreg, ctypes.wintypes, and SendInput Win32 APIs
- Python 3.12 (based on the .pyc filenames)

# Security Features
Encryption
- All usernames and passwords are encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256) from Python's
cryptography library — the raw values never touch the database
- The encryption key is derived from the master password via PBKDF2-HMAC-SHA256 with 600,000 iterations and a 32-byte
random salt — this matches current NIST recommendations for brute-force resistance

Authentication model
- Instead of prompting for a password on every launch, the app uses a device key — a secrets.token_urlsafe(48) value
stored in ~/.password_manager/device.key. This ties the vault to the machine without requiring user interaction
- The vault won't open if the device key file is missing, and the UI displays a clear error rather than falling back
silently

API security
- The local REST API requires an X-Api-Key header on every request matching the device key — unauthenticated requests
get a 401
- The API only binds to 127.0.0.1, so it's never reachable over the network
- The browser extension's manifest.json restricts host_permissions to only http://127.0.0.1:7412/*

Timing-safe comparison
- unlock() uses secrets.compare_digest() to compare the derived key hash, preventing timing attacks (vault.py:69)

Clipboard auto-clear
- Copied passwords are automatically cleared from the clipboard after 30 seconds (CLIP_TTL = 30 in gui.py)

Single-instance enforcement
- single_instance.py prevents multiple concurrent processes, reducing the attack surface from race conditions or
duplicate API servers

Notable gap worth knowing about: the device key is stored as plaintext on disk — if an attacker has filesystem access,
both the key and the encrypted database are in the same directory (~/.password_manager/), so physical/OS-level access
bypasses all encryption.
