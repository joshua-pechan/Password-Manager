"""Device-local authentication key — generated once, stored encrypted via DPAPI.

The key blob is only decryptable by the same Windows user account, so copying
the vault.db + device.key to another machine or OS is not sufficient to decrypt
credentials.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import re
import secrets
from pathlib import Path

_PATH = Path.home() / ".password_manager" / "device.key"

# ── DPAPI wrappers ────────────────────────────────────────────────────────────

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _protect(data: bytes) -> bytes:
    buf      = (ctypes.c_byte * len(data))(*data)
    blob_in  = _Blob(len(data), buf)
    blob_out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0,
        ctypes.byref(blob_out),
    ):
        raise OSError(f"CryptProtectData failed (error {ctypes.GetLastError()})")
    try:
        return bytes(blob_out.pbData[:blob_out.cbData])
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _unprotect(data: bytes) -> bytes:
    buf      = (ctypes.c_byte * len(data))(*data)
    blob_in  = _Blob(len(data), buf)
    blob_out = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0,
        ctypes.byref(blob_out),
    ):
        raise OSError(f"CryptUnprotectData failed (error {ctypes.GetLastError()})")
    try:
        return bytes(blob_out.pbData[:blob_out.cbData])
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


# ── Public API ────────────────────────────────────────────────────────────────

_PLAINTEXT_RE = re.compile(r'^[A-Za-z0-9_\-]{40,}$')


def path() -> Path:
    return _PATH


def exists() -> bool:
    return _PATH.exists()


def load() -> str:
    """Return the device key, creating and DPAPI-encrypting a new one on first call.

    Transparently migrates plaintext keys written by older versions.
    """
    _PATH.parent.mkdir(exist_ok=True, parents=True)

    if not _PATH.exists():
        key = secrets.token_urlsafe(48)
        _PATH.write_bytes(_protect(key.encode()))
        return key

    raw = _PATH.read_bytes()

    # Migration: if the file still holds an old plaintext token, re-encrypt it.
    try:
        candidate = raw.decode("ascii").strip()
        if _PLAINTEXT_RE.match(candidate):
            _PATH.write_bytes(_protect(candidate.encode()))
            return candidate
    except (UnicodeDecodeError, ValueError):
        pass

    return _unprotect(raw).decode()
