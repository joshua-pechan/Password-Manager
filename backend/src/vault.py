from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from database import get_connection, init_db

_KDF_ITERATIONS = 600_000


def _extract_domain(url: str) -> str:
    """Return the registrable domain from a URL, stripping www."""
    if "://" not in url:
        url = f"https://{url}"
    host = urlparse(url).netloc or urlparse(url).path.split("/")[0]
    return host[4:] if host.startswith("www.") else host


class VaultError(Exception):
    pass


class Vault:
    def __init__(self) -> None:
        init_db()
        self._fernet: Fernet | None = None

    # ── Setup / auth ─────────────────────────────────────────────────────────

    def is_initialized(self) -> bool:
        with get_connection() as conn:
            return conn.execute(
                "SELECT 1 FROM vault_meta WHERE key = 'password_hash'"
            ).fetchone() is not None

    def setup(self, master_password: str) -> None:
        if self.is_initialized():
            raise VaultError("Vault is already initialized")
        salt = secrets.token_bytes(32)
        key = self._derive_key(master_password, salt)
        with get_connection() as conn:
            conn.execute("INSERT INTO vault_meta VALUES ('salt', ?)",
                         (base64.b64encode(salt).decode(),))
            conn.execute("INSERT INTO vault_meta VALUES ('password_hash', ?)",
                         (hashlib.sha256(key).hexdigest(),))
            conn.commit()
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def unlock(self, master_password: str) -> bool:
        with get_connection() as conn:
            salt_row = conn.execute(
                "SELECT value FROM vault_meta WHERE key = 'salt'"
            ).fetchone()
            hash_row = conn.execute(
                "SELECT value FROM vault_meta WHERE key = 'password_hash'"
            ).fetchone()
        if not salt_row or not hash_row:
            return False
        salt = base64.b64decode(salt_row["value"])
        key = self._derive_key(master_password, salt)
        if secrets.compare_digest(hashlib.sha256(key).hexdigest(), hash_row["value"]):
            self._fernet = Fernet(base64.urlsafe_b64encode(key))
            return True
        return False

    def lock(self) -> None:
        self._fernet = None

    def is_unlocked(self) -> bool:
        return self._fernet is not None

    def change_master_password(self, old_password: str, new_password: str) -> None:
        if not self.unlock(old_password):
            raise VaultError("Incorrect current password")
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM credentials").fetchall()
        new_salt = secrets.token_bytes(32)
        new_key = self._derive_key(new_password, new_salt)
        new_fernet = Fernet(base64.urlsafe_b64encode(new_key))
        with get_connection() as conn:
            for row in rows:
                new_uname = new_fernet.encrypt(
                    self._fernet.decrypt(row["username_enc"].encode())
                ).decode()
                new_pass = new_fernet.encrypt(
                    self._fernet.decrypt(row["password_enc"].encode())
                ).decode()
                conn.execute(
                    "UPDATE credentials SET username_enc = ?, password_enc = ? WHERE id = ?",
                    (new_uname, new_pass, row["id"]),
                )
            conn.execute("UPDATE vault_meta SET value = ? WHERE key = 'salt'",
                         (base64.b64encode(new_salt).decode(),))
            conn.execute("UPDATE vault_meta SET value = ? WHERE key = 'password_hash'",
                         (hashlib.sha256(new_key).hexdigest(),))
            conn.commit()
        self._fernet = new_fernet

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def save(self, url: str, username: str, password: str, group_name: str = '') -> int:
        """Save a web credential."""
        self._require_unlocked()
        domain = _extract_domain(url)
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO credentials"
                " (domain, url, username_enc, password_enc, cred_type, app_name, group_name)"
                " VALUES (?, ?, ?, ?, 'web', '', ?)",
                (domain, url,
                 self._fernet.encrypt(username.encode()).decode(),
                 self._fernet.encrypt(password.encode()).decode(),
                 group_name),
            )
            conn.commit()
            return cursor.lastrowid

    def save_app(self, app_name: str, username: str, password: str, group_name: str = '') -> int:
        """Save a desktop app credential."""
        self._require_unlocked()
        domain = app_name.lower().strip()
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO credentials"
                " (domain, url, username_enc, password_enc, cred_type, app_name, group_name)"
                " VALUES (?, '', ?, ?, 'app', ?, ?)",
                (domain,
                 self._fernet.encrypt(username.encode()).decode(),
                 self._fernet.encrypt(password.encode()).decode(),
                 app_name, group_name),
            )
            conn.commit()
            return cursor.lastrowid

    def get_by_url(self, url: str) -> list[dict]:
        self._require_unlocked()
        domain = _extract_domain(url)
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM credentials WHERE domain = ? AND cred_type = 'web'",
                (domain,)
            ).fetchall()
        return [self._decrypt_row(r) for r in rows]

    def get_by_app(self, window_title: str) -> list[dict]:
        """Return app credentials whose stored name appears in the window title."""
        self._require_unlocked()
        title_lower = window_title.lower()
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM credentials WHERE cred_type = 'app'"
            ).fetchall()
        return [
            self._decrypt_row(r) for r in rows
            if r["domain"] in title_lower
        ]

    def get_by_id(self, cred_id: int) -> dict | None:
        self._require_unlocked()
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM credentials WHERE id = ?", (cred_id,)
            ).fetchone()
        return self._decrypt_row(row) if row else None

    def get_all(self) -> list[dict]:
        self._require_unlocked()
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM credentials ORDER BY cred_type, domain"
            ).fetchall()
        return [self._decrypt_row(r) for r in rows]

    def update(
        self,
        cred_id: int,
        username: str | None = None,
        password: str | None = None,
        url: str | None = None,
        app_name: str | None = None,
        group_name: str | None = None,
        cred_type: str | None = None,
    ) -> bool:
        self._require_unlocked()
        updates, params = [], []
        if username is not None:
            updates.append("username_enc = ?")
            params.append(self._fernet.encrypt(username.encode()).decode())
        if password is not None:
            updates.append("password_enc = ?")
            params.append(self._fernet.encrypt(password.encode()).decode())
        if url is not None:
            updates.append("url = ?")
            params.append(url)
            updates.append("domain = ?")
            params.append(_extract_domain(url))
        if app_name is not None:
            updates.append("app_name = ?")
            params.append(app_name)
            updates.append("domain = ?")
            params.append(app_name.lower().strip())
        if group_name is not None:
            updates.append("group_name = ?")
            params.append(group_name)
        if cred_type is not None:
            updates.append("cred_type = ?")
            params.append(cred_type)
        if not updates:
            return False
        updates.append("updated_at = datetime('now')")
        params.append(cred_id)
        with get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE credentials SET {', '.join(updates)} WHERE id = ?", params
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete(self, cred_id: int) -> bool:
        self._require_unlocked()
        with get_connection() as conn:
            cursor = conn.execute("DELETE FROM credentials WHERE id = ?", (cred_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ── Custom-tab management ────────────────────────────────────────────────

    def get_tab_order(self) -> list[str]:
        """Return full tab order (built-ins + custom). Falls back to default."""
        import json
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM vault_meta WHERE key = 'tab_order'"
            ).fetchone()
            custom = [r["name"] for r in conn.execute(
                "SELECT name FROM tabs ORDER BY position, created_at"
            ).fetchall()]
        if row:
            try:
                saved = json.loads(row["value"])
                valid = {"web", "app"} | set(custom)
                order = [k for k in saved if k in valid]
                for key in ("web", "app"):
                    if key not in order:
                        order.insert(0, key)
                for c in custom:
                    if c not in order:
                        order.append(c)
                return order
            except Exception:
                pass
        return ["web", "app"] + custom

    def set_tab_order(self, order: list[str]) -> None:
        import json
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO vault_meta (key, value) VALUES ('tab_order', ?)",
                (json.dumps(order),)
            )
            conn.commit()

    def get_custom_tabs(self) -> list[str]:
        """Return custom tab names in position order."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM tabs ORDER BY position, created_at"
            ).fetchall()
        return [r["name"] for r in rows]

    def create_custom_tab(self, name: str) -> None:
        with get_connection() as conn:
            row = conn.execute("SELECT MAX(position) FROM tabs").fetchone()
            max_pos = row[0] if row[0] is not None else 0
            conn.execute(
                "INSERT OR IGNORE INTO tabs (name, position) VALUES (?, ?)",
                (name, max_pos + 1),
            )
            conn.commit()

    def delete_custom_tab(self, name: str) -> None:
        """Delete custom tab; its credentials revert to cred_type='web'."""
        with get_connection() as conn:
            conn.execute(
                "UPDATE credentials SET cred_type = 'web', updated_at = datetime('now')"
                " WHERE cred_type = ?",
                (name,),
            )
            conn.execute("DELETE FROM tabs WHERE name = ?", (name,))
            # Remove from saved order
            import json
            row = conn.execute(
                "SELECT value FROM vault_meta WHERE key = 'tab_order'"
            ).fetchone()
            if row:
                try:
                    order = [k for k in json.loads(row["value"]) if k != name]
                    conn.execute(
                        "UPDATE vault_meta SET value = ? WHERE key = 'tab_order'",
                        (json.dumps(order),)
                    )
                except Exception:
                    pass
            conn.commit()

    def save_to_tab(self, label: str, username: str, password: str,
                    tab_name: str, group_name: str = '') -> int:
        """Save a credential to a custom tab."""
        self._require_unlocked()
        domain = label.lower().strip()
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO credentials"
                " (domain, url, username_enc, password_enc, cred_type, app_name, group_name)"
                " VALUES (?, '', ?, ?, ?, ?, ?)",
                (domain,
                 self._fernet.encrypt(username.encode()).decode(),
                 self._fernet.encrypt(password.encode()).decode(),
                 tab_name, label, group_name),
            )
            conn.commit()
            return cursor.lastrowid

    # ── Group management ──────────────────────────────────────────────────────

    def create_group(self, name: str, cred_type: str = 'web') -> None:
        """Create a named group (persists even when empty)."""
        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO groups (name, cred_type) VALUES (?, ?)",
                (name, cred_type),
            )
            conn.commit()

    def get_groups(self, cred_type: str = 'web') -> list[str]:
        """Return all explicitly-created group names for the given type."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM groups WHERE cred_type = ? ORDER BY name",
                (cred_type,),
            ).fetchall()
        return [r["name"] for r in rows]

    def delete_group(self, name: str, cred_type: str = 'web') -> None:
        """Remove a group and ungroup all its credentials (group_name → '')."""
        with get_connection() as conn:
            conn.execute(
                "UPDATE credentials SET group_name = '', updated_at = datetime('now')"
                " WHERE group_name = ?",
                (name,),
            )
            conn.execute(
                "DELETE FROM groups WHERE name = ? AND cred_type = ?",
                (name, cred_type),
            )
            conn.commit()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _require_unlocked(self) -> None:
        if not self._fernet:
            raise VaultError("Vault is locked")

    @staticmethod
    def _derive_key(password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=_KDF_ITERATIONS,
        )
        return kdf.derive(password.encode())

    def _decrypt_row(self, row: sqlite3.Row) -> dict:
        keys = row.keys()
        return {
            "id":         row["id"],
            "domain":     row["domain"],
            "url":        row["url"],
            "app_name":   row["app_name"]   if "app_name"   in keys else "",
            "cred_type":  row["cred_type"]  if "cred_type"  in keys else "web",
            "group_name": row["group_name"] if "group_name" in keys else "",
            "username":   self._fernet.decrypt(row["username_enc"].encode()).decode(),
            "password":   self._fernet.decrypt(row["password_enc"].encode()).decode(),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


