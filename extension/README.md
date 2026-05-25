# Chrome Extension

A Manifest V3 Chrome extension that auto-fills passwords and saves new credentials,
talking to the local API server at `http://127.0.0.1:7412`.

Install by loading the `extension/` folder as an unpacked extension in `chrome://extensions`
(Developer mode must be on).

---

## Files

| File | Purpose |
|---|---|
| `manifest.json` | Extension metadata, permissions, declares content script and service worker. |
| `background.js` | Service worker. Stores the API key, makes all API calls to the local server. Content scripts and the popup never hold the key directly. |
| `content.js` | Runs on every page. Handles auto-fill and credential capture on form submission. |
| `popup.html/js/css` | The toolbar popup UI — three tabs: Logins, Generator, Settings. |
| `icons/` | 16px, 48px, 128px PNG icons. |

---

## First-time setup

1. Open the desktop GUI → click **Info** → copy the API key.
2. Open the extension popup → **Settings** tab → paste the key → **Save**.

The key is stored in `chrome.storage.local` and sent as an `X-Api-Key` header on every request.

---

## Auto-fill

When you visit a login page that has a saved credential:

1. The content script detects `input[type="password"]` fields on the page.
2. It asks the background worker for credentials matching the current domain.
3. **One match:** fills username and password silently.
4. **Multiple matches:** uses the displayed email on the page (or whichever account you clicked in an account picker) to select the right credential. Falls back to the popup Fill button if it can't determine which account.

**Multi-step login support (Google, Microsoft):**
- If the email and password are on separate steps, the script tracks the email across steps using hidden inputs, cross-step memory, and visible page text.
- After an account picker (e.g. Google's "Choose an account"), the clicked account's email is extracted from the DOM to ensure the right password is filled.
- If the password field is animating in (not yet visible), the script polls every 150ms until it appears, then fills.
- SPA navigation (URL changes without a page reload) is detected via `MutationObserver` and triggers a re-check.

---

## Credential saving

When you log into a site that isn't saved yet:

1. The content script detects form submission via three triggers (in order of reliability):
   - DOM `submit` event (traditional forms)
   - Click on a submit-looking button (React/SPA forms)
   - `Enter` key pressed in a login field
2. Username and password are captured. The username is found from: visible inputs → hidden inputs with email-format values → text/email values typed earlier in the session.
3. A pending credential is stored in `chrome.storage.local` (5-minute TTL).
4. A **save banner** appears at the top of the page:

```
🔑  Save password for github.com?   you@example.com   [Save]  [Not now]  [✕]
```

5. On traditional (full-page) logins, the banner reappears on the post-login page so you have time to interact with it. Domain matching is broad enough to handle cross-subdomain redirects (e.g. `accounts.google.com` → `mail.google.com`).
6. MFA / verification pages are detected and skipped — the banner only appears once you're fully logged in.
7. If a credential with the same username already exists for that domain, no banner is shown.

---

## Password Generator (popup → Generator tab)

- Slider: 8–20 characters (default 20).
- Checkboxes: Uppercase, Lowercase, Numbers, Symbols (all on by default).
- Cryptographically random using `crypto.getRandomValues`.
- Strength bar: Weak / Fair / Strong / Very Strong based on length and character variety.
- Copy button copies to clipboard.

---

## Popup — Logins tab

- Shows connection status (server running? API key set? Vault unlocked?).
- Lists saved credentials matching the current tab's domain.
- **Fill** button injects the credential into the active tab's login form.

---

## Permissions used

| Permission | Why |
|---|---|
| `storage` | Stores the API key and pending-save credential across sessions. |
| `activeTab` | Reads the current tab's URL for credential lookup. |
| `scripting` | Re-injects the content script into tabs that were open before the extension was installed. |
| `host_permissions: http://127.0.0.1:7412/*` | Allows fetch calls to the local API server. |
