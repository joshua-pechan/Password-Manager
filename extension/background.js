/**
 * Background service worker — all API communication goes through here so
 * content scripts and the popup never need to hold the API key themselves.
 */

const API = 'http://127.0.0.1:7412';

// ── Key helpers ───────────────────────────────────────────────────────────────

async function getKey() {
  const { apiKey } = await chrome.storage.local.get('apiKey');
  return apiKey || null;
}

// ── API helpers ───────────────────────────────────────────────────────────────

async function apiFetch(path, options = {}) {
  const key = await getKey();
  if (!key) throw new Error('NO_KEY');

  const resp = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Api-Key': key,
      ...(options.headers || {}),
    },
    signal: AbortSignal.timeout(4000),
  });

  if (resp.status === 401) throw new Error('BAD_KEY');
  return resp;
}

// ── Message handlers ──────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  switch (msg.type) {

    case 'GET_CREDENTIALS':
      apiFetch(`/credentials?url=${encodeURIComponent(msg.url)}`)
        .then(r => r.json())
        .then(d => reply({ ok: true,  credentials: d.credentials || [] }))
        .catch(e => reply({ ok: false, error: e.message, credentials: [] }));
      return true; // async reply

    case 'SAVE_CREDENTIAL':
      apiFetch('/credentials', {
        method: 'POST',
        body: JSON.stringify(msg.credential),
      })
        .then(r => reply({ ok: r.ok }))
        .catch(e => reply({ ok: false, error: e.message }));
      return true;

    case 'DELETE_CREDENTIAL':
      apiFetch(`/credentials/${msg.id}`, { method: 'DELETE' })
        .then(r => reply({ ok: r.ok }))
        .catch(e => reply({ ok: false, error: e.message }));
      return true;

    case 'CHECK_STATUS': {
      const check = async () => {
        try {
          const resp = await fetch(`${API}/health`, {
            signal: AbortSignal.timeout(2000),
          });
          const data = await resp.json();
          const key  = await getKey();
          return { connected: true, unlocked: data.unlocked, hasKey: !!key };
        } catch {
          return { connected: false, unlocked: false, hasKey: !!(await getKey()) };
        }
      };
      check().then(reply);
      return true;
    }

    case 'VERIFY_KEY': {
      const verify = async () => {
        try {
          const r = await fetch(`${API}/credentials`, {
            headers: { 'X-Api-Key': msg.key },
            signal: AbortSignal.timeout(3000),
          });
          return { ok: r.status !== 401 };
        } catch {
          // Server offline — store it anyway, can't verify right now
          return { ok: true, serverOffline: true };
        }
      };
      verify().then(async result => {
        if (result.ok) await chrome.storage.local.set({ apiKey: msg.key });
        reply(result);
      });
      return true;
    }
  }
});
