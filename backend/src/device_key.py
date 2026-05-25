"""Device-local authentication key — generated once, stored in the user profile."""
from __future__ import annotations

import secrets
from pathlib import Path

_PATH = Path.home() / ".password_manager" / "device.key"


def path() -> Path:
    return _PATH


def exists() -> bool:
    return _PATH.exists()


def load() -> str:
    """Return the device key, creating a new one on first call."""
    _PATH.parent.mkdir(exist_ok=True, parents=True)
    if not _PATH.exists():
        _PATH.write_text(secrets.token_urlsafe(48))
    return _PATH.read_text().strip()
