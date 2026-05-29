from __future__ import annotations

import sqlite3
from pathlib import Path

_DB_DIR = Path.home() / ".password_manager"
DB_PATH = _DB_DIR / "vault.db"


def get_connection() -> sqlite3.Connection:
    _DB_DIR.mkdir(exist_ok=True, parents=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS vault_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS credentials (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                domain       TEXT NOT NULL,
                url          TEXT NOT NULL,
                username_enc TEXT NOT NULL,
                password_enc TEXT NOT NULL,
                created_at   TEXT DEFAULT (datetime('now')),
                updated_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_credentials_domain
                ON credentials(domain);

            CREATE TABLE IF NOT EXISTS groups (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                cred_type  TEXT NOT NULL DEFAULT 'web',
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(name, cred_type)
            );

            CREATE TABLE IF NOT EXISTS tabs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                position   INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add new columns to existing databases without losing data."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(credentials)")}
    if "cred_type" not in existing:
        conn.execute(
            "ALTER TABLE credentials ADD COLUMN cred_type TEXT NOT NULL DEFAULT 'web'"
        )
    if "app_name" not in existing:
        conn.execute(
            "ALTER TABLE credentials ADD COLUMN app_name TEXT NOT NULL DEFAULT ''"
        )
    if "group_name" not in existing:
        conn.execute(
            "ALTER TABLE credentials ADD COLUMN group_name TEXT NOT NULL DEFAULT ''"
        )
    conn.commit()
