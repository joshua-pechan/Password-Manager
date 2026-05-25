'use strict';

let _tabUrl = '';

// ── Boot ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  bindTabs();
  bindGenerator();

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  _tabUrl = tab?.url || '';

  await refreshStatus();
  await loadCredentials();
});

// ── Tabs ──────────────────────────────────────────────────────────────────────

function bindTabs() {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const target = btn.dataset.tab;
      document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
      document.getElementById(`view-${target}`).classList.remove('hidden');
    });
  });
}

// ── Status ────────────────────────────────────────────────────────────────────

async function refreshStatus() {
  const bar    = document.getElementById('status-bar');
  const status = await chrome.runtime.sendMessage({ type: 'CHECK_STATUS' });

  if (!status.connected) {
    bar.textContent = '✕  Password Manager is not running';
    bar.className   = 'status error';
  } else if (!status.hasKey) {
    bar.textContent = '⚠  API key not set — go to Settings tab';
    bar.className   = 'status warn';
  } else if (!status.unlocked) {
    bar.textContent = '⚠  Vault locked — open the desktop app';
    bar.className   = 'status warn';
  } else {
    bar.textContent = '●  Connected';
    bar.className   = 'status ok';
  }
}

// ── Credentials ───────────────────────────────────────────────────────────────

async function loadCredentials() {
  if (!_tabUrl || _tabUrl.startsWith('chrome://') || _tabUrl.startsWith('about:')) return;

  const resp = await chrome.runtime.sendMessage({ type: 'GET_CREDENTIALS', url: _tabUrl });
  renderCredentials(resp?.credentials || []);
}

const AVATAR_PALETTE = [
  '#5b8dee','#8b5cf6','#ec4899','#10b981','#f59e0b','#06b6d4','#ef4444','#84cc16',
];
function avatarColor(str) {
  if (!str) return AVATAR_PALETTE[0];
  return AVATAR_PALETTE[str.toUpperCase().charCodeAt(0) % AVATAR_PALETTE.length];
}

function renderCredentials(creds) {
  const list  = document.getElementById('cred-list');
  const empty = document.getElementById('no-creds');
  list.innerHTML = '';

  if (!creds.length) { empty.classList.remove('hidden'); return; }
  empty.classList.add('hidden');

  for (const cred of creds) {
    const row = document.createElement('div');
    row.className = 'cred-row';

    const avatar = document.createElement('div');
    avatar.className = 'cred-avatar';
    avatar.style.background = avatarColor(cred.username || '');
    avatar.textContent = (cred.username || '?')[0].toUpperCase();

    const info = document.createElement('div');
    info.className = 'cred-info';
    info.innerHTML = `
      <div class="cred-user">${esc(cred.username)}</div>
      <div class="cred-domain">${esc(cred.domain || '')}</div>
    `;

    const actions = document.createElement('div');
    actions.className = 'cred-actions';

    const copyBtn = document.createElement('button');
    copyBtn.className = 'icon-btn';
    copyBtn.title = 'Copy password';
    copyBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
    copyBtn.addEventListener('click', () => {
      if (cred.password) navigator.clipboard.writeText(cred.password);
    });

    const fillBtn = document.createElement('button');
    fillBtn.className = 'fill-btn';
    fillBtn.textContent = 'Fill';
    fillBtn.addEventListener('click', () => fillCredential(cred));

    actions.appendChild(copyBtn);
    actions.appendChild(fillBtn);
    row.appendChild(avatar);
    row.appendChild(info);
    row.appendChild(actions);
    list.appendChild(row);
  }
}

async function fillCredential(cred) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] });
  } catch { /* already injected or restricted page */ }
  chrome.tabs.sendMessage(tab.id, { type: 'FILL_CREDENTIAL', credential: cred });
  window.close();
}

// ── Settings ──────────────────────────────────────────────────────────────────

document.getElementById('btn-save-key').addEventListener('click', async () => {
  const key = document.getElementById('key-input').value.trim();
  const msg = document.getElementById('key-msg');

  if (!key) { msg.textContent = 'Key cannot be empty.'; msg.className = 'hint error'; return; }

  const result = await chrome.runtime.sendMessage({ type: 'VERIFY_KEY', key });
  if (!result.ok) {
    msg.textContent = '✕  Invalid key — check the Info dialog in the desktop app.';
    msg.className   = 'hint error';
    return;
  }
  msg.textContent = result.serverOffline ? '✓  Saved (server offline — will verify on next use)' : '✓  Saved and verified';
  msg.className = 'hint ok';

  setTimeout(async () => {
    // Switch back to Logins tab
    document.querySelector('.tab[data-tab="main"]').click();
    await refreshStatus();
    await loadCredentials();
  }, 900);
});

// Pre-fill textarea when settings tab is shown
document.querySelector('.tab[data-tab="settings"]').addEventListener('click', async () => {
  const { apiKey } = await chrome.storage.local.get('apiKey');
  if (apiKey) document.getElementById('key-input').value = apiKey;
});

// ── Password generator ────────────────────────────────────────────────────────

const CHARS = {
  upper:   'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
  lower:   'abcdefghijklmnopqrstuvwxyz',
  digits:  '0123456789',
  symbols: '!@#$%^&*()-_=+[]{}|;:,.<>?',
};

function generatePassword(length, opts) {
  let pool = '';
  const required = [];

  if (opts.upper)   { pool += CHARS.upper;   required.push(randomChar(CHARS.upper));   }
  if (opts.lower)   { pool += CHARS.lower;   required.push(randomChar(CHARS.lower));   }
  if (opts.digits)  { pool += CHARS.digits;  required.push(randomChar(CHARS.digits));  }
  if (opts.symbols) { pool += CHARS.symbols; required.push(randomChar(CHARS.symbols)); }

  if (!pool) return '';

  const arr = new Uint32Array(length);
  crypto.getRandomValues(arr);
  const chars = Array.from(arr, n => pool[n % pool.length]);

  // Guarantee at least one character from each selected set
  required.forEach((ch, i) => { chars[i] = ch; });

  // Fisher-Yates shuffle
  for (let i = chars.length - 1; i > 0; i--) {
    const j = crypto.getRandomValues(new Uint32Array(1))[0] % (i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }

  return chars.join('');
}

function randomChar(str) {
  return str[crypto.getRandomValues(new Uint32Array(1))[0] % str.length];
}

function passwordStrength(pwd) {
  if (!pwd) return { pct: 0, label: '', col: '#555' };
  let score = 0;
  if (pwd.length >= 12) score++;
  if (pwd.length >= 16) score++;
  if (pwd.length >= 20) score++;
  if (/[A-Z]/.test(pwd)) score++;
  if (/[a-z]/.test(pwd)) score++;
  if (/[0-9]/.test(pwd)) score++;
  if (/[^A-Za-z0-9]/.test(pwd)) score++;

  if (score <= 2) return { pct: 20,  label: 'Weak',   col: '#ef4444' };
  if (score <= 4) return { pct: 50,  label: 'Fair',   col: '#f97316' };
  if (score <= 5) return { pct: 75,  label: 'Strong', col: '#eab308' };
  return            { pct: 100, label: 'Very strong', col: '#22c55e' };
}

function bindGenerator() {
  const lengthSlider  = document.getElementById('gen-length');
  const lengthVal     = document.getElementById('gen-length-val');
  const output        = document.getElementById('gen-output');
  const strengthBar   = document.getElementById('gen-strength');
  const strengthLabel = document.getElementById('gen-strength-label');
  const copiedMsg     = document.getElementById('gen-copied');

  // Keep length label in sync
  lengthSlider.addEventListener('input', () => {
    lengthVal.textContent = lengthSlider.value;
  });

  function getOpts() {
    return {
      upper:   document.getElementById('opt-upper').checked,
      lower:   document.getElementById('opt-lower').checked,
      digits:  document.getElementById('opt-digits').checked,
      symbols: document.getElementById('opt-symbols').checked,
    };
  }

  document.getElementById('btn-generate').addEventListener('click', () => {
    const opts = getOpts();
    if (!Object.values(opts).some(Boolean)) return; // nothing selected

    const pwd = generatePassword(Number(lengthSlider.value), opts);
    output.value = pwd;

    const s = passwordStrength(pwd);
    strengthBar.style.setProperty('--pct', s.pct + '%');
    strengthBar.style.setProperty('--col', s.col);
    strengthBar.title = s.label;
    strengthLabel.textContent = s.label;
    strengthLabel.style.color = s.col;

    copiedMsg.classList.add('hidden');
  });

  document.getElementById('btn-copy-gen').addEventListener('click', () => {
    if (!output.value) return;
    navigator.clipboard.writeText(output.value).then(() => {
      copiedMsg.classList.remove('hidden');
      setTimeout(() => copiedMsg.classList.add('hidden'), 2000);
    });
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
