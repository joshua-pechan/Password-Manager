# Backend

The backend is a local Flask API server and a customtkinter desktop GUI.
Both share the same encrypted SQLite database.

---

## File locations

### Project files

```
backend/
├── gui.py              ← desktop GUI (customtkinter)
├── main.py             ← server-only entry point (used by startup task)
├── startup.py          ← registers a Windows Task Scheduler logon task
├── requirements.txt
└── src/
    ├── server.py           ← Flask REST API
    ├── vault.py            ← encryption + all database CRUD
    ├── database.py         ← SQLite connection + schema init
    ├── device_key.py       ← device key generation and loading
    ├── autofill.py         ← Windows desktop app autofill (Ctrl+Shift+F hotkey)
    └── single_instance.py  ← Windows named mutex (prevents duplicate processes)
```

### Data files

All data lives in your Windows user profile — outside the project folder so it survives
if you move or reinstall the project.

| Path | Description |
|---|---|
| `C:\Users\<you>\.password_manager\vault.db` | SQLite database. Contains the `credentials` table (all saved logins, fully encrypted) and `vault_meta` table (salt + password verification hash). |
| `C:\Users\<you>\.password_manager\device.key` | 64-character random key. This is the only secret. **Back it up.** If it's deleted, the vault cannot be decrypted. |
| `C:\Users\<you>\.password_manager\config.json` | GUI preferences stored as JSON (e.g. `start_in_tray`). Created automatically on first preference change. |
| `C:\Users\<you>\.password_manager\gui.ipc` | Ephemeral file written at GUI startup containing the IPC socket port. Used so a second GUI launch signals the first to come to front instead of opening a duplicate. Deleted/recreated on each launch. |

---

## How the encryption works

1. On first run, `device.key` is generated (`secrets.token_urlsafe(48)`) and saved.
2. The key is run through **PBKDF2-HMAC-SHA256** (600,000 iterations) to derive a 32-byte encryption key.
3. All usernames and passwords are encrypted with **Fernet** (AES-128-CBC + HMAC-SHA256) before being written to the database.
4. The domain and URL are stored as plaintext for fast lookup — only credentials are encrypted.
5. A SHA-256 hash of the derived key is stored in `vault_meta` for verification on unlock. The key itself is never stored.

---

## API server

- **Address:** `http://127.0.0.1:7412` (localhost only — unreachable from other devices)
- **Auth:** every request (except `GET /health`) must include `X-Api-Key: <contents of device.key>`
- **Started by:** the GUI on launch, or independently via `python main.py`

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Server status. No auth required. |
| `GET` | `/credentials` | List all saved credentials. |
| `GET` | `/credentials?url=<url>` | List credentials matching that domain. |
| `GET` | `/credentials?app=<title>` | List app credentials whose name appears in the given window title. |
| `GET` | `/credentials/<id>` | Get a single credential by ID. |
| `POST` | `/credentials` | Save a new credential. See body formats below. |
| `PUT` | `/credentials/<id>` | Update an existing credential. |
| `DELETE` | `/credentials/<id>` | Delete a credential. |

### POST /credentials body formats

**Web credential:**
```json
{ "cred_type": "web", "url": "https://github.com", "username": "you@example.com", "password": "secret" }
```

**App credential:**
```json
{ "cred_type": "app", "app_name": "Discord", "username": "you@example.com", "password": "secret" }
```

`cred_type` defaults to `"web"` if omitted.

---

## Running the server and GUI

| Command | What it does |
|---|---|
| `python gui.py` | Opens the desktop GUI. Also starts the API server as a background thread if it isn't already running. |
| `python main.py` | Starts the API server only (no GUI). Used by the startup task. Exits silently if already running. |
| `python startup.py` | Registers a Windows Task Scheduler logon task that runs `main.py` at login using `pythonw.exe` (no console window). Run once. |
| `python startup.py --remove` | Removes the startup task. |
| `build.bat` (project root) | Runs `build.py` to produce `dist/PasswordManager_Setup.exe`. |

---

## Single-instance behaviour

Both the server and GUI use a **Windows named mutex** (`Local\PasswordManager_server` and
`Local\PasswordManager_gui`) to prevent duplicate processes.

- A second server launch exits immediately.
- A second GUI launch sends a signal to the running instance via a local socket, causing it
  to restore from the system tray, then exits.

---

## GUI behaviour

- Opens directly to the credential list — no login prompt.
- Closing the window minimizes to the **system tray** (lock icon). Right-click the tray icon for Open / Quit.
- The credential list polls credential timestamps every 2 seconds and refreshes automatically when credentials are added externally (e.g. via the Chrome extension).
- The **Info** button shows file paths and the API key (needed for the Chrome extension setup).

### Tabs

The GUI organizes credentials into tabs:

- **Websites** — web logins (matched by domain)
- **Apps** — desktop app logins (matched by window title for autofill)
- **Custom tabs** — create your own (e.g. PIN Codes, Wi-Fi, Notes) via the **+** button in the tab bar

Tabs can be dragged to reorder. Custom tabs can be deleted; their credentials move back to Websites.

### Groups

Within any tab, credentials can be organized into named groups:

- Click **+ Group** to create a named group (e.g. "Work", "Gaming")
- Drag credentials onto a group header to add them to the group
- Groups with multiple credentials are collapsible
- Use **Expand All / Collapse All** to toggle all groups at once

### Password Generator

Click **Generator** in the toolbar to open the generator dialog:

- Slider: 8–64 characters
- Checkboxes: Uppercase, Lowercase, Numbers, Symbols
- Strength indicator (Weak / Fair / Strong / Very Strong)
- Uses `secrets.choice` (cryptographically random)

### Desktop App Autofill

Press **Ctrl+Shift+F** while a desktop application window is focused. The autofill engine
checks the active window title against saved app credentials and fills the foreground window's
username/password fields automatically.

---

## Transferring passwords

The **Transfer** button in the toolbar opens a menu:

### Export to CSV

Saves all credentials to a CSV file with columns: `Title, URL, Username, Password, Notes, Type`.
**Delete the file immediately after use — it contains all passwords in plain text.**

### Import from CSV

Imports credentials from a CSV file. Column detection is case-insensitive and accepts partial
matches, so exports from Chrome, Firefox, Bitwarden, and 1Password are all supported.
Rows missing a username or password are skipped.

### Send to Phone

Opens a dialog with a **QR code** and one-time URL. Phone must be on the same Wi-Fi network.
Scanning the QR code downloads the CSV directly. The link expires after 60 seconds and works
for a single download only.

---

## Dependencies

```
flask>=3.0.0           ← REST API
cryptography>=42.0.0   ← PBKDF2 + Fernet encryption
customtkinter>=5.2.0   ← desktop GUI framework
pystray>=0.19.0        ← system tray icon
pillow>=10.0.0         ← icon rendering + QR code display
keyboard>=0.13.5       ← global hotkey for desktop app autofill
qrcode[pil]>=7.4.0     ← QR code generation for Send to Phone
```
