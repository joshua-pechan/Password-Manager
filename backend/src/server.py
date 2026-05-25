"""Local REST API server — authenticated via a device key file, not a password."""
from __future__ import annotations

import logging
from functools import wraps

from flask import Flask, jsonify, request

from vault import Vault, VaultError
from device_key import load as _load_key

HOST = "127.0.0.1"
PORT = 7412

app   = Flask(__name__)
vault = Vault()

_api_key: str = ""


# ── Auth ──────────────────────────────────────────────────────────────────────

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.headers.get("X-Api-Key") != _api_key:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# ── CORS (localhost + Chrome extension origins only) ──────────────────────────

@app.after_request
def _cors(response):
    origin = request.headers.get("Origin", "")
    if (
        origin.startswith("chrome-extension://")
        or origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")
    ):
        response.headers["Access-Control-Allow-Origin"]  = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Api-Key"
    return response


@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def _preflight(path=""):
    return "", 204


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return jsonify({"status": "ok", "unlocked": vault.is_unlocked()})


@app.get("/credentials")
@require_auth
def list_credentials():
    url      = request.args.get("url")
    app_name = request.args.get("app")
    if url:
        creds = vault.get_by_url(url)
    elif app_name:
        creds = vault.get_by_app(app_name)
    else:
        creds = vault.get_all()
    return jsonify({"credentials": creds})


@app.get("/credentials/<int:cred_id>")
@require_auth
def get_credential(cred_id: int):
    cred = vault.get_by_id(cred_id)
    return jsonify(cred) if cred else (jsonify({"error": "Not found"}), 404)


@app.post("/credentials")
@require_auth
def create_credential():
    data      = request.get_json(silent=True) or {}
    cred_type = data.get("cred_type", "web")

    if cred_type == "app":
        missing = [f for f in ("app_name", "username", "password") if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing required fields: {missing}"}), 400
        cred_id = vault.save_app(data["app_name"], data["username"], data["password"])
    else:
        missing = [f for f in ("url", "username", "password") if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing required fields: {missing}"}), 400
        cred_id = vault.save(data["url"], data["username"], data["password"],
                             data.get("group_name", ""))

    return jsonify({"id": cred_id}), 201


@app.put("/credentials/<int:cred_id>")
@require_auth
def update_credential(cred_id: int):
    data    = request.get_json(silent=True) or {}
    success = vault.update(
        cred_id,
        username=data.get("username"),
        password=data.get("password"),
        url=data.get("url"),
        app_name=data.get("app_name"),
        group_name=data.get("group_name"),
    )
    return jsonify({"message": "Updated"}) if success else (jsonify({"error": "Not found"}), 404)


@app.delete("/credentials/<int:cred_id>")
@require_auth
def delete_credential(cred_id: int):
    return (
        jsonify({"message": "Deleted"}) if vault.delete(cred_id)
        else (jsonify({"error": "Not found"}), 404)
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def run(host: str = HOST, port: int = PORT) -> None:
    global _api_key
    _api_key = _load_key()

    if not vault.is_initialized():
        vault.setup(_api_key)
    elif not vault.is_unlocked():
        vault.unlock(_api_key)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    print(f"Password Manager API running on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)
