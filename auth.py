"""
auth.py — Authentication for Entradas Ticket Scout.

Security features:
  - werkzeug PBKDF2_SHA256 password hashing (salted + iterated)
  - Flask signed-cookie sessions (tamper-proof, SECRET_KEY-protected)
  - IP-based login rate limiting (max 5 failed attempts per 60s)
  - before_request whitelist — ALL routes protected unless explicitly public
  - Session expiry (configurable, default 8 hours)
"""

import logging
import os
import secrets
import threading
import time
from collections import defaultdict
from datetime import timedelta
from functools import wraps

from flask import redirect, request, session, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

# ── Rate limiting ──────────────────────────────────────────────────────────────

_failed: defaultdict = defaultdict(list)   # ip → [timestamps]
_lock   = threading.Lock()
MAX_ATTEMPTS = 5
WINDOW_SECS  = 60


def is_rate_limited(ip: str) -> bool:
    now = time.time()
    with _lock:
        recent = [t for t in _failed[ip] if now - t < WINDOW_SECS]
        _failed[ip] = recent
        return len(recent) >= MAX_ATTEMPTS


def record_failed(ip: str):
    with _lock:
        _failed[ip].append(time.time())


def clear_failed(ip: str):
    with _lock:
        _failed[ip] = []


def remaining_lockout(ip: str) -> int:
    """Seconds until lockout expires. 0 if not locked out."""
    now = time.time()
    with _lock:
        recent = [t for t in _failed[ip] if now - t < WINDOW_SECS]
        if len(recent) < MAX_ATTEMPTS:
            return 0
        oldest = min(recent)
        return max(0, int(WINDOW_SECS - (now - oldest)) + 1)


# ── Secret key management ──────────────────────────────────────────────────────

def ensure_secret_key(env_path: str) -> str:
    """
    Returns the SECRET_KEY from .env.
    If none exists, generates one and persists it to .env.
    This is called once at startup so sessions survive restarts.
    """
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=env_path, override=True)
    key = os.getenv("SECRET_KEY", "")
    if not key:
        key = secrets.token_hex(32)
        with open(env_path, "a") as f:
            f.write(f"\nSECRET_KEY={key}\n")
        logger.info("Generated new SECRET_KEY and saved to .env")
    return key


# ── Password utilities ─────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return generate_password_hash(plain, method="pbkdf2:sha256", salt_length=16)


def verify_password(plain: str, hashed: str) -> bool:
    return check_password_hash(hashed, plain)


# ── Default admin bootstrap ────────────────────────────────────────────────────

def ensure_default_admin():
    """
    On first run (no users in DB), create default admin / admin123.
    Logs a prominent warning so the owner knows to change it.
    """
    import database as _db
    users = _db.list_users()
    if not users:
        _db.create_user("admin", hash_password("admin123"), role="admin")
        logger.warning(
            "⚠️  DEFAULT ADMIN CREATED — username: admin  password: admin123\n"
            "   CHANGE THIS IMMEDIATELY in Settings → User Management!"
        )


# ── Session helpers ────────────────────────────────────────────────────────────

SESSION_LIFETIME_HOURS = 8


def login_user(user_id: int, username: str):
    session.permanent  = True
    session["user_id"] = user_id
    session["username"] = username


def logout_user():
    session.clear()


def current_user_id():
    return session.get("user_id")


def current_username() -> str:
    return session.get("username", "")


# ── Public routes (everything else requires login) ─────────────────────────────

PUBLIC_ENDPOINTS = {"login", "logout", "static", "change_password"}


def check_auth():
    """
    before_request handler.
    Whitelist approach: only PUBLIC_ENDPOINTS skip the check.
    Every other route (including all /api/*) requires a valid session.
    Also enforces password change when force_pw_change flag is set.
    """
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not session.get("user_id"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Unauthorized", "login_required": True}), 401
        next_url = request.path
        return redirect(f"/login?next={next_url}")
    # Force password change for default credentials
    if session.get("force_pw_change") and request.endpoint not in PUBLIC_ENDPOINTS:
        if not request.path.startswith("/api/"):
            return redirect("/change-password?reason=default")
    return None
