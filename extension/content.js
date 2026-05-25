/**
 * Content script — auto-fill saved logins, capture new logins on submit.
 *
 * Handles multi-step login flows (Google-style: email → password → MFA):
 *   - Searches hidden inputs for username when the email field has been animated away
 *   - Tracks the last-filled text/email value as a cross-step fallback
 *   - Skips showing the save banner on MFA / verification pages
 */

(function () {
  'use strict';

  if (window.self !== window.top) return;

  // ── Helpers ─────────────────────────────────────────────────────────────────

  function isVisible(el) {
    if (!el.offsetParent && el.offsetWidth === 0 && el.offsetHeight === 0) return false;
    const s = window.getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  }

  function extractDomain(url) {
    try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; }
  }

  /** Last two labels of a hostname — e.g. "accounts.google.com" → "google.com". */
  function baseDomain(host) {
    const parts = host.split('.');
    return parts.length >= 2 ? parts.slice(-2).join('.') : host;
  }

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Persistent storage (chrome.storage.local + 5-min TTL) ───────────────────

  const PENDING_KEY = 'pm_pending';
  const PENDING_TTL = 5 * 60 * 1000;

  async function storePending(credential) {
    await chrome.storage.local
      .set({ [PENDING_KEY]: { ...credential, ts: Date.now() } })
      .catch(() => {});
  }

  async function getPending() {
    const data  = await chrome.storage.local.get(PENDING_KEY).catch(() => ({}));
    const entry = data[PENDING_KEY];
    if (!entry) return null;
    if (Date.now() - entry.ts > PENDING_TTL) { await clearPending(); return null; }
    return entry;
  }

  async function clearPending() {
    await chrome.storage.local.remove(PENDING_KEY).catch(() => {});
  }

  // ── Cross-step username tracking ─────────────────────────────────────────────
  // On multi-step flows (Google, Microsoft) the email field disappears before
  // the password step. We track the last username via two mechanisms:
  //   1. Input blur/change — captures typed email addresses
  //   2. Picker clicks — captures the email when the user clicks an account
  //      card (Google's "Choose an account" page has no text inputs, just
  //      clickable list items containing the email address as visible text)

  let _lastUsername = null;

  function trackUsernameAcrossSteps() {
    // Track typed values
    ['blur', 'change'].forEach(evt => {
      document.addEventListener(evt, (event) => {
        const el = event.target;
        if (!el.matches?.('input')) return;
        const t = (el.type || '').toLowerCase();
        if ((t === 'text' || t === 'email' || t === '') && el.value.trim()) {
          _lastUsername = el.value.trim();
        }
      }, true);
    });

    // Track account-picker clicks (Google / Microsoft chooser UIs)
    document.addEventListener('click', (event) => {
      // Walk up to 6 levels from the click target looking for an element
      // whose short text contains a single email address.
      let el = event.target;
      for (let depth = 0; depth < 6 && el && el !== document.body; depth++, el = el.parentElement) {
        const text = (el.innerText || el.textContent || '').trim();
        if (text.length > 200) continue;          // too large — skip
        const m = text.match(/\b([\w.+\-]+@[\w.\-]+\.\w{2,})\b/);
        if (m) {
          _lastUsername = m[1];
          return;
        }
      }
    }, true);
  }

  // ── Login field detection ────────────────────────────────────────────────────

  function findLoginPairs() {
    const pwFields = Array.from(document.querySelectorAll('input[type="password"]'))
      .filter(isVisible);
    return pwFields.map(pwField => {
      const allInputs = Array.from(document.querySelectorAll('input')).filter(isVisible);
      const pwIdx = allInputs.indexOf(pwField);
      let usernameField = null;
      for (let i = pwIdx - 1; i >= 0; i--) {
        const t = allInputs[i].type.toLowerCase();
        if (t === 'text' || t === 'email' || t === '') { usernameField = allInputs[i]; break; }
      }
      return { usernameField, passwordField: pwField };
    });
  }

  /**
   * Extract {url, username, password} from the current page state.
   * Uses three fallback strategies to handle multi-step login flows:
   *   1. Visible text/email input before the password field  (standard)
   *   2. Any text/email input in the DOM, even if hidden     (Google-style)
   *   3. _lastUsername tracked from an earlier step          (full page transitions)
   */
  function captureLoginFields() {
    const pwFields = Array.from(document.querySelectorAll('input[type="password"]'))
      .filter(isVisible);

    // 1 field = login; 2 fields = registration (password + confirm); 3+ = skip
    if (pwFields.length === 0 || pwFields.length > 2) return null;

    // Registration: both fields must be non-empty and matching
    if (pwFields.length === 2) {
      if (!pwFields[0].value || pwFields[0].value !== pwFields[1].value) return null;
    }

    const password = pwFields[0].value;
    if (!password) return null;

    let username = null;

    // Strategy 1: visible username field in DOM order before the password field
    const visibleInputs = Array.from(document.querySelectorAll('input')).filter(isVisible);
    const pwIdx = visibleInputs.indexOf(pwFields[0]);
    for (let i = pwIdx - 1; i >= 0; i--) {
      const t = visibleInputs[i].type.toLowerCase();
      if ((t === 'text' || t === 'email' || t === '') && visibleInputs[i].value.trim()) {
        username = visibleInputs[i].value.trim();
        break;
      }
    }

    // Strategy 2: any input in the DOM (including hidden) whose value matches an
    // email format.  Catches Google's hidden email field after the step animation
    // AND full-page-navigation cases where _lastUsername has been reset.
    if (!username) {
      const allInputs = document.querySelectorAll('input');
      for (const input of allInputs) {
        if (input === pwFields[0]) continue;
        const v = input.value.trim();
        if (v && /^[\w.+\-]+@[\w.\-]+\.\w{2,}$/.test(v)) {
          username = v;
          break;
        }
      }
    }

    // Strategy 3: username captured from a previous page / step
    if (!username && _lastUsername) {
      username = _lastUsername;
    }

    if (!username) return null;
    return { url: window.location.href, username, password };
  }

  // ── MFA / verification page detection ───────────────────────────────────────

  function looksLikeVerificationPage() {
    // If there's a visible password field this is still a login page, not MFA
    if (document.querySelector('input[type="password"]')) return false;

    // OTP / 2FA inputs
    const otpSelectors = [
      'input[inputmode="numeric"]',
      'input[autocomplete="one-time-code"]',
      'input[name*="otp"]',
      'input[name*="totp"]',
      'input[name*="code"]',
      'input[name*="token"]',
      'input[name*="mfa"]',
      'input[name*="2fa"]',
    ].join(', ');

    const otpInputs = Array.from(document.querySelectorAll(otpSelectors)).filter(isVisible);
    if (otpInputs.length > 0) return true;

    // URL pattern matching
    return /\/challenge\/|\/mfa|\/2fa|\/verify|\/otp|\/totp|\/sms|two.?factor|two.?step/i
      .test(window.location.href);
  }

  // ── Fill ─────────────────────────────────────────────────────────────────────

  function fill(usernameField, passwordField, credential) {
    if (usernameField) setNativeValue(usernameField, credential.username);
    setNativeValue(passwordField, credential.password);
  }

  function setNativeValue(el, value) {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    if (setter?.set) setter.set.call(el, value); else el.value = value;
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // ── Save banner ──────────────────────────────────────────────────────────────

  function showSaveBanner(credential) {
    document.getElementById('__pm_save_banner__')?.remove();

    const domain = extractDomain(credential.url);
    const banner = document.createElement('div');
    banner.id = '__pm_save_banner__';

    banner.innerHTML = `
      <style>
        #__pm_save_banner__ {
          all: initial !important;
          position: fixed !important;
          top: 0 !important; left: 0 !important; right: 0 !important;
          z-index: 2147483647 !important;
          display: flex !important; flex-direction: column !important;
          background: #1f2937 !important;
          box-shadow: 0 4px 16px rgba(0,0,0,0.6) !important;
          font-family: 'Segoe UI', system-ui, sans-serif !important;
          font-size: 13px !important; color: #f9fafb !important;
          box-sizing: border-box !important;
          overflow: hidden !important;
        }
        #__pm_save_banner__ .pm-row {
          display: flex !important; align-items: center !important; gap: 10px !important;
          padding: 10px 16px !important;
        }
        #__pm_save_banner__ .pm-icon  { font-size: 17px; flex-shrink: 0; }
        #__pm_save_banner__ .pm-msg   { flex: 1; line-height: 1.4; }
        #__pm_save_banner__ .pm-site  { color: #60a5fa; font-weight: 600; }
        #__pm_save_banner__ .pm-user  { color: #9ca3af; font-size: 11px; }
        #__pm_save_banner__ button {
          all: initial !important;
          font-family: inherit !important; font-size: 12px !important;
          padding: 6px 14px !important; border-radius: 5px !important; cursor: pointer !important;
          white-space: nowrap !important;
        }
        #__pm_save_banner__ .pm-save {
          background: #3b82f6 !important; color: #fff !important; font-weight: 600 !important;
        }
        #__pm_save_banner__ .pm-save:hover { background: #2563eb !important; }
        #__pm_save_banner__ .pm-skip {
          background: transparent !important; color: #9ca3af !important;
          border: 1px solid #4b5563 !important;
        }
        #__pm_save_banner__ .pm-skip:hover { background: #374151 !important; color: #fff !important; }
        #__pm_save_banner__ .pm-close {
          all: initial !important; cursor: pointer !important;
          color: #6b7280 !important; font-size: 18px !important; padding: 2px 4px !important;
          font-family: inherit !important;
        }
        #__pm_save_banner__ .pm-close:hover { color: #d1d5db !important; }
        #__pm_save_banner__ .pm-progress {
          height: 2px !important;
          background: #3b82f6 !important;
          width: 100% !important;
          transform-origin: left center !important;
          animation: pm-shrink 25s linear forwards !important;
        }
        @keyframes pm-shrink {
          from { transform: scaleX(1); }
          to   { transform: scaleX(0); }
        }
      </style>
      <div class="pm-row">
        <span class="pm-icon">🔑</span>
        <span class="pm-msg">
          <span>Save password for <span class="pm-site">${esc(domain)}</span>?</span><br>
          <span class="pm-user">${esc(credential.username)}</span>
        </span>
        <button class="pm-save">Save</button>
        <button class="pm-skip">Not now</button>
        <button class="pm-close" title="Dismiss">✕</button>
      </div>
      <div class="pm-progress"></div>
    `;

    document.body.prepend(banner);

    const dismiss = async (save) => {
      banner.remove();
      await clearPending();
      if (!save) return;
      const resp = await chrome.runtime.sendMessage({
        type: 'SAVE_CREDENTIAL', credential,
      }).catch(() => null);
      if (!resp?.ok) showToast('Save failed — make sure the Password Manager desktop app is running', 'error');
      else showToast(`Saved login for ${domain}`);
    };

    banner.querySelector('.pm-save').addEventListener('click',  () => dismiss(true));
    banner.querySelector('.pm-skip').addEventListener('click',  () => dismiss(false));
    banner.querySelector('.pm-close').addEventListener('click', () => dismiss(false));
    setTimeout(() => banner.remove(), 25000);
  }

  function showToast(msg, type = 'ok') {
    const t = document.createElement('div');
    Object.assign(t.style, {
      position: 'fixed', bottom: '20px', right: '20px', zIndex: '2147483647',
      background: type === 'error' ? '#7f1d1d' : '#14532d',
      color: type === 'error' ? '#fca5a5' : '#bbf7d0',
      padding: '10px 16px', borderRadius: '7px',
      fontSize: '12px', fontFamily: 'system-ui, sans-serif',
      boxShadow: '0 2px 10px rgba(0,0,0,0.45)', maxWidth: '320px',
    });
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 4000);
  }

  // ── Credential capture (three triggers) ──────────────────────────────────────

  async function handleCapture() {
    const cred = captureLoginFields();
    if (!cred) return;
    await storePending(cred);
    showSaveBanner(cred);
  }

  function looksLikeLoginButton(btn) {
    if (btn.type === 'submit') return true;
    const label = [btn.textContent, btn.value, btn.getAttribute('aria-label'), btn.getAttribute('title')]
      .filter(Boolean).join(' ').toLowerCase().trim();
    if (/\b(log\s?in|sign\s?in|login|signin|sign\s?up|signup|register|create|submit|continue|next|enter|verify|proceed|get\s?started)\b/.test(label)) return true;
    const form = btn.closest('form');
    if (form && form.querySelector('input[type="password"]')) {
      if (/forgot|reset|help|cancel|close|back|show|hide|toggle/.test(label)) return false;
      if (label.length > 2) return true;
    }
    return false;
  }

  function listenForCredentialCapture() {
    document.addEventListener('submit', () => handleCapture(), true);

    document.addEventListener('click', (event) => {
      const btn = event.target.closest('button, input[type="submit"], input[type="button"]');
      if (btn && looksLikeLoginButton(btn)) handleCapture();
    }, true);

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      const el = document.activeElement;
      if (!el) return;
      const t = el.type?.toLowerCase();
      if (t === 'text' || t === 'email' || t === 'password') handleCapture();
    }, true);
  }

  // ── Check for pending save from previous page ────────────────────────────────

  async function checkPendingSave() {
    const pending = await getPending();
    if (!pending) return;

    const pendingDomain = extractDomain(pending.url);
    const currentDomain = extractDomain(window.location.href);
    const domainMatch =
      pendingDomain === currentDomain ||
      currentDomain.endsWith('.' + pendingDomain) ||
      pendingDomain.endsWith('.' + currentDomain) ||
      baseDomain(pendingDomain) === baseDomain(currentDomain); // accounts.google.com ↔ mail.google.com
    if (!domainMatch) return;

    // Still on the login page (e.g. wrong password) — don't show yet
    if (window.location.href === pending.url) return;

    // On an MFA / verification step — wait until the user is fully logged in
    if (looksLikeVerificationPage()) return;

    await clearPending();

    // Use the original login URL for the duplicate check so credentials saved under
    // accounts.google.com are still found when we're now on mail.google.com
    const resp = await chrome.runtime.sendMessage({
      type: 'GET_CREDENTIALS', url: pending.url,
    }).catch(() => null);
    if ((resp?.credentials || []).some(c => c.username === pending.username)) return;

    showSaveBanner(pending);
  }

  // ── Auto-fill ────────────────────────────────────────────────────────────────

  let _filled   = false;
  let _retrying = false; // prevent overlapping retry chains

  async function tryAutoFill() {
    if (_filled) return;

    const pairs = findLoginPairs();

    if (!pairs.length) {
      // No *visible* password fields, but check if one exists in the DOM
      // and is just mid-animation (e.g. Google SPA account-picker → password step).
      // If so, poll until it becomes visible rather than giving up.
      if (!_retrying && document.querySelector('input[type="password"]')) {
        _retrying = true;
        const poll = () => {
          if (_filled) { _retrying = false; return; }
          if (findLoginPairs().length) {
            _retrying = false;
            tryAutoFill();   // field is visible now — do the real fill
          } else {
            setTimeout(poll, 150);
          }
        };
        setTimeout(poll, 150);
      }
      return;
    }

    _retrying = false;
    _filled = true; // lock before the await so concurrent callbacks don't stack up

    const response = await chrome.runtime.sendMessage({
      type: 'GET_CREDENTIALS', url: window.location.href,
    }).catch(() => null);

    const creds = response?.credentials;
    if (!creds?.length) return;

    const cred = _pickCredential(creds);
    if (cred) {
      _lastUsername = null; // consumed — reset so it doesn't affect future fills
      fill(pairs[0].usernameField, pairs[0].passwordField, cred);
    }
    // If no single credential can be determined, the user can fill via the popup.
  }

  /**
   * Try to determine which email address is the target for this login page
   * without relying on _lastUsername (which is reset on full page navigation).
   * Checks every input (including hidden) for an email-format value, then
   * falls back to scanning visible page text.
   */
  function _readEmailFromPage() {
    for (const input of document.querySelectorAll('input')) {
      const v = input.value.trim();
      if (v && /^[\w.+\-]+@[\w.\-]+\.\w{2,}$/.test(v)) return v;
    }
    // Visible text — matches the first email-like string on the page
    // (e.g. Google's "jashduck@gmail.com" account chip)
    const m = (document.body?.innerText || '').match(
      /\b([\w.+\-]+@[\w.\-]+\.\w{2,})\b/
    );
    return m ? m[1] : null;
  }

  /**
   * Choose which credential to auto-fill.
   * Priority: _lastUsername (SPA/picker) → email read from page → single-match fallback.
   */
  function _pickCredential(creds) {
    const hint = _lastUsername || _readEmailFromPage();
    if (hint) {
      const match = creds.find(
        c => c.username.toLowerCase() === hint.toLowerCase()
      );
      if (match) return match;
      // Email identified on page but not in vault — don't fill the wrong account
      return null;
    }
    return creds.length === 1 ? creds[0] : null;
  }

  // ── Boot ─────────────────────────────────────────────────────────────────────

  tryAutoFill();
  checkPendingSave();
  listenForCredentialCapture();
  trackUsernameAcrossSteps();

  // Retry auto-fill for password fields that are in the DOM but still animating
  // in (Google, Microsoft, etc. use CSS transitions so the MutationObserver
  // won't re-fire once the field is already present but not yet visible).
  [600, 1500, 3000].forEach(ms =>
    setTimeout(() => { if (!_filled) tryAutoFill(); }, ms)
  );

  // Watch for DOM changes. Also detects SPA navigation by comparing the URL
  // on each mutation — when it changes, reset state and re-run page-load logic
  // so checkPendingSave fires after every client-side navigation (e.g. Google's
  // account picker → password page → Gmail, all within one content script).
  let _lastUrl = window.location.href;

  const _obs = new MutationObserver(() => {
    const url = window.location.href;
    if (url !== _lastUrl) {
      // SPA navigation: new "page" loaded without a real browser navigation
      _lastUrl   = url;
      _filled    = false;
      _retrying  = false;
      tryAutoFill();
      checkPendingSave();
    } else if (!_filled) {
      tryAutoFill();
    }
  });

  if (document.body) {
    _obs.observe(document.body, { childList: true, subtree: true });
  } else {
    document.addEventListener('DOMContentLoaded', () =>
      _obs.observe(document.body, { childList: true, subtree: true }));
  }

  // ── Popup-triggered fill ──────────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg.type !== 'FILL_CREDENTIAL') return;
    const pairs = findLoginPairs();
    if (!pairs.length) { reply({ ok: false, error: 'No login fields found' }); return; }
    fill(pairs[0].usernameField, pairs[0].passwordField, msg.credential);
    reply({ ok: true });
  });

})();
