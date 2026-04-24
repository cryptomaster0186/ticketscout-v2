"""
app.py — TicketScout v2 Web Dashboard (Flask)

Pokretanje:
    python app.py              # http://localhost:5000
    python app.py --port 8080  # custom port
    python app.py --host 0.0.0.0  # accessible on network/VPS
"""

import argparse
import json
import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime, timedelta

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, stream_with_context, url_for)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import database as db
import auth as _auth
from ticket_routes import ticket_bp
from accounts_routes import accounts_bp

app = Flask(__name__)
app.register_blueprint(ticket_bp)
app.register_blueprint(accounts_bp)

# Persistent secret key — generated once, saved to .env
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
app.secret_key = _auth.ensure_secret_key(_env_path)
app.permanent_session_lifetime = timedelta(hours=_auth.SESSION_LIFETIME_HOURS)

# ── Security config ───────────────────────────────────────────────────────────
_PRODUCTION = os.getenv("PRODUCTION", "false").lower() in ("1", "true", "yes")

app.config.update(
    SESSION_COOKIE_HTTPONLY  = True,                 # JS cannot read session cookie
    SESSION_COOKIE_SAMESITE  = "Lax",                # CSRF protection
    SESSION_COOKIE_SECURE    = _PRODUCTION,          # HTTPS-only in production
    SESSION_COOKIE_NAME      = "ts_session",         # non-default name (minor hardening)
    MAX_CONTENT_LENGTH       = 16 * 1024 * 1024,     # 16 MB max upload
    PROPAGATE_EXCEPTIONS     = False,
)

# ── Security headers — applied to every response ──────────────────────────────
@app.after_request
def _security_headers(response):
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]         = "DENY"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]      = "geolocation=(), microphone=(), camera=()"
    if _PRODUCTION:
        # Only send HSTS on real HTTPS — never in local dev
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Tight CSP: allow only same-origin + the exact CDNs already used
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' "
        "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    # Remove server fingerprint headers
    response.headers.pop("Server", None)
    response.headers.pop("X-Powered-By", None)
    return response

# ── Auth guard — applied to EVERY request ─────────────────────────────────────
app.before_request(_auth.check_auth)

# ── Log queue (za SSE stream u UI) ────────────────────────────────────────────

_log_queue: queue.Queue = queue.Queue(maxsize=500)


class _QueueHandler(logging.Handler):
    """Pored normalnog logiranja, šalje poruke u SSE queue."""
    LEVEL_CLASS = {
        "DEBUG":    "debug",
        "INFO":     "info",
        "WARNING":  "warning",
        "ERROR":    "error",
        "CRITICAL": "error",
    }

    def emit(self, record: logging.LogRecord):
        try:
            _log_queue.put_nowait({
                "level": self.LEVEL_CLASS.get(record.levelname, "info"),
                "msg":   self.format(record),
                "ts":    datetime.utcnow().strftime("%H:%M:%S"),
            })
        except queue.Full:
            pass


_queue_handler = _QueueHandler()
_queue_handler.setFormatter(logging.Formatter("%(name)s | %(message)s"))
logging.getLogger().addHandler(_queue_handler)
logger = logging.getLogger(__name__)


# ── Scheduler state ────────────────────────────────────────────────────────────

_scheduler_running  = False
_scheduler_thread = None  # type: threading.Thread
_last_task_status   = {
    "discovery": "never",
    "snapshot":  "never",
    "restock":   "never",
    "analysis":  "never",
}


def _run_task_bg(name: str, fn, *args):
    """Pokreće task u background threadu i ažurira status."""
    def _run():
        _last_task_status[name] = "running"
        logger.info(f"▶ Task started: {name}")
        try:
            fn(*args)
            _last_task_status[name] = datetime.utcnow().strftime("%H:%M:%S")
            logger.info(f"✅ Task done: {name}")
        except Exception as e:
            _last_task_status[name] = f"error: {e}"
            logger.error(f"❌ Task failed {name}: {e}", exc_info=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _scheduler_loop():
    """Scheduler koji radi kao daemon thread."""
    global _scheduler_running
    tick = 0
    logger.info("🚀 Scheduler started")

    # Lazy import da izbjegnemo import error ako API keys nisu postavljeni
    try:
        import tm_client
        import secondary_market as sm
        import restock_monitor
        import analyzer
        import discord_alerts
    except ImportError as e:
        logger.error(f"Import error in scheduler: {e}")
        _scheduler_running = False
        return

    # Initial discovery
    try:
        db.init_db()
        _run_task_bg("discovery", tm_client.discover_all)
    except Exception as e:
        logger.error(f"Initial discovery error: {e}")

    while _scheduler_running:
        time.sleep(60)
        tick += 1

        if not _scheduler_running:
            break

        try:
            if tick % 5 == 0:
                _run_task_bg("restock", restock_monitor.run_restock_check)
            if tick % 15 == 0:
                pass  # snapshot — needs more context, triggered via UI
            if tick % 60 == 0:
                _run_task_bg("discovery", tm_client.discover_all)
        except Exception as e:
            logger.error(f"Scheduler tick error: {e}")

    logger.info("⏹ Scheduler stopped")


# ── API Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html",
                           current_user=_auth.current_username())


# ── Auth routes ────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        ip       = request.remote_addr or "unknown"
        username = (request.form.get("username") or "").strip().lower()
        password =  request.form.get("password") or ""

        # Rate limit check
        if _auth.is_rate_limited(ip):
            secs = _auth.remaining_lockout(ip)
            error = f"Too many failed attempts. Try again in {secs}s."
        else:
            user = db.get_user_by_username(username)
            if user and _auth.verify_password(password, user["password_hash"]):
                _auth.clear_failed(ip)
                _auth.login_user(user["id"], user["username"])
                db.update_last_login(user["id"])
                # Force password change if still using the default password
                if password == "admin123":
                    session["force_pw_change"] = True
                    return redirect("/change-password?reason=default")
                next_url = request.args.get("next", "/")
                return redirect(next_url)
            else:
                _auth.record_failed(ip)
                attempts_left = _auth.MAX_ATTEMPTS - len(
                    [t for t in _auth._failed.get(ip, [])
                     if time.time() - t < _auth.WINDOW_SECS]
                )
                if attempts_left > 0:
                    error = f"Invalid username or password. {attempts_left} attempt{'s' if attempts_left != 1 else ''} remaining."
                else:
                    error = f"Too many failed attempts. Locked out for {_auth.WINDOW_SECS}s."
                logger.warning(f"Failed login for '{username}' from {ip}")

    if session.get("user_id"):
        return redirect("/")

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    _auth.logout_user()
    return redirect("/login")


@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    """Force-password-change page — shown when default credentials are detected."""
    if not session.get("user_id"):
        return redirect("/login")

    error   = None
    success = None
    reason  = request.args.get("reason", "")

    if request.method == "POST":
        new_pw  = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if len(new_pw) < 8:
            error = "Password must be at least 8 characters."
        elif new_pw != confirm:
            error = "Passwords do not match."
        elif new_pw == "admin123":
            error = "Choose a password that is not the default."
        else:
            uid = session["user_id"]
            db.update_user_password(uid, _auth.hash_password(new_pw))
            session.pop("force_pw_change", None)
            logger.info(f"Password changed for user_id={uid}")
            return redirect("/?pw_changed=1")

    return render_template(
        "change_password.html",
        error=error, success=success, reason=reason
    )


# ── User management API ────────────────────────────────────────────────────────

@app.route("/api/users")
def api_users_list():
    db.init_db()
    rows = db.list_users()
    return jsonify([
        {"id": r["id"], "username": r["username"], "role": r["role"],
         "created_at": r["created_at"], "last_login": r["last_login"]}
        for r in rows
    ])


@app.route("/api/users", methods=["POST"])
def api_users_create():
    db.init_db()
    data     = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()
    role     = data.get("role", "user")

    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if role not in ("admin", "user"):
        role = "user"

    existing = db.get_user_by_username(username)
    if existing:
        return jsonify({"error": f"Username '{username}' already exists"}), 409

    uid = db.create_user(username, _auth.hash_password(password), role=role)
    logger.info(f"User created: {username} (role={role})")
    return jsonify({"status": "created", "id": uid})


@app.route("/api/users/<int:user_id>/password", methods=["POST"])
def api_users_change_password(user_id: int):
    db.init_db()
    data        = request.get_json() or {}
    new_password = (data.get("password") or "").strip()

    if len(new_password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    db.update_user_password(user_id, _auth.hash_password(new_password))
    logger.info(f"Password changed for user id={user_id}")
    return jsonify({"status": "updated"})


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
def api_users_delete(user_id: int):
    db.init_db()
    # Cannot delete yourself
    if user_id == session.get("user_id"):
        return jsonify({"error": "Cannot delete your own account"}), 400
    # Must keep at least one admin
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    if user["role"] == "admin" and db.count_admin_users() <= 1:
        return jsonify({"error": "Cannot delete the last admin account"}), 400

    db.delete_user(user_id)
    logger.info(f"User deleted: id={user_id}")
    return jsonify({"status": "deleted"})


@app.route("/api/me")
def api_me():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "id":         user["id"],
        "username":   user["username"],
        "role":       user["role"],
        "last_login": user["last_login"],
    })


@app.route("/api/stats")
def api_stats():
    try:
        db.init_db()
        stats = db.get_stats()
        stats["scheduler_running"] = _scheduler_running
        stats["last_tasks"] = _last_task_status
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/events")
def api_events():
    """Vraća listu evenata s filterima."""
    try:
        db.init_db()
        country         = request.args.get("country", "")
        status          = request.args.get("status", "")
        search          = request.args.get("q", "").lower()
        page            = int(request.args.get("page", 1))
        per_page        = int(request.args.get("per_page", 50))
        show_dismissed  = request.args.get("show_dismissed", "0") == "1"

        with db.get_conn() as conn:
            where_clauses = ["1=1"]
            params = []

            if not show_dismissed:
                where_clauses.append("(e.dismissed IS NULL OR e.dismissed = 0)")
            if country:
                where_clauses.append("e.country = ?")
                params.append(country)
            if status:
                where_clauses.append("e.status = ?")
                params.append(status)
            if search:
                where_clauses.append("(LOWER(e.artist) LIKE ? OR LOWER(e.city) LIKE ?)")
                params += [f"%{search}%", f"%{search}%"]

            where_sql = " AND ".join(where_clauses)

            total = conn.execute(
                f"SELECT COUNT(*) FROM events e WHERE {where_sql}", params
            ).fetchone()[0]

            offset = (page - 1) * per_page
            rows = conn.execute(f"""
                SELECT e.*,
                    a.verdict, a.demand_score, a.resale_potential, a.sellout_eta,
                    s.secondary_low, s.spread_pct, s.secondary_source, s.ts as snap_ts
                FROM events e
                LEFT JOIN (
                    SELECT event_id, verdict, demand_score, resale_potential, sellout_eta
                    FROM analysis ORDER BY ts DESC
                ) a ON a.event_id = e.id
                LEFT JOIN (
                    SELECT event_id, secondary_low, spread_pct,
                           secondary_source, ts
                    FROM snapshots ORDER BY ts DESC
                ) s ON s.event_id = e.id
                WHERE {where_sql}
                ORDER BY e.event_date ASC
                LIMIT ? OFFSET ?
            """, params + [per_page, offset]).fetchall()

        return jsonify({
            "total":    total,
            "page":     page,
            "per_page": per_page,
            "events":   [dict(r) for r in rows],
        })
    except Exception as e:
        logger.error(f"api_events error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/events/<int:event_id>")
def api_event_detail(event_id: int):
    """Vraća detalje jednog eventa s price historijom."""
    try:
        db.init_db()
        event = db.get_event_by_id(event_id)
        if not event:
            return jsonify({"error": "Not found"}), 404

        snapshots = db.get_recent_snapshots(event_id, limit=50)
        analysis  = db.get_latest_analysis(event_id)

        with db.get_conn() as conn:
            all_analysis = conn.execute(
                "SELECT * FROM analysis WHERE event_id=? ORDER BY ts DESC LIMIT 10",
                (event_id,)
            ).fetchall()

        return jsonify({
            "event":      dict(event),
            "snapshots":  [dict(s) for s in snapshots],
            "analysis":   dict(analysis) if analysis else None,
            "all_analysis": [dict(a) for a in all_analysis],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/events/<int:event_id>/dismiss", methods=["POST"])
def api_event_dismiss(event_id: int):
    try:
        db.init_db()
        db.dismiss_event(event_id)
        return jsonify({"status": "dismissed"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/events/<int:event_id>/undismiss", methods=["POST"])
def api_event_undismiss(event_id: int):
    try:
        db.init_db()
        db.undismiss_event(event_id)
        return jsonify({"status": "undismissed"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sidebar-stats")
def api_sidebar_stats():
    """Lightweight stats for sidebar badge counts."""
    try:
        db.init_db()
        stats = db.get_sidebar_stats()

        # Pending ticket deliveries from Google Sheets (best-effort)
        pending_deliveries = 0
        unpaid_payouts = 0
        try:
            import sheets_service as ss
            tickets = ss.getTickets()
            pending_deliveries = sum(
                1 for t in tickets
                if (t.get("deliveryStatus") or "").upper() == "PENDING"
            )
            unpaid_payouts = sum(
                1 for t in tickets
                if (t.get("payoutStatus") or "").upper() in ("UNPAID", "PENDING")
            )
        except Exception:
            pass

        # Upcoming expenses this week
        expenses_this_week = 0
        try:
            import recurring_expenses as re_svc
            upcoming = re_svc.getUpcomingExpenses(7)
            expenses_this_week = len(upcoming)
        except Exception:
            pass

        stats["pending_deliveries"] = pending_deliveries
        stats["unpaid_payouts"]     = unpaid_payouts
        stats["expenses_this_week"] = expenses_this_week
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/track")
def api_alerts_track():
    try:
        db.init_db()
        with db.get_conn() as conn:
            rows = conn.execute("""
                SELECT a.*, e.artist, e.city, e.country, e.event_date,
                       e.venue, e.tm_url, e.face_value_min, e.face_value_max,
                       s.secondary_low, s.spread_pct
                FROM analysis a
                JOIN events e ON a.event_id = e.id
                LEFT JOIN (
                    SELECT event_id, secondary_low, spread_pct
                    FROM snapshots ORDER BY ts DESC
                ) s ON s.event_id = e.id
                WHERE a.verdict = 'TRACK'
                ORDER BY a.ts DESC
                LIMIT 100
            """).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/restock")
def api_alerts_restock():
    try:
        db.init_db()
        with db.get_conn() as conn:
            rows = conn.execute("""
                SELECT r.*, e.artist, e.city, e.country, e.event_date,
                       e.venue, e.tm_url, e.face_value_min
                FROM restock_alerts r
                JOIN events e ON r.event_id = e.id
                ORDER BY r.detected_at DESC
                LIMIT 50
            """).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scheduler/start", methods=["POST"])
def api_scheduler_start():
    global _scheduler_running, _scheduler_thread
    if _scheduler_running:
        return jsonify({"status": "already_running"})
    _scheduler_running = True
    _scheduler_thread  = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()
    return jsonify({"status": "started"})


@app.route("/api/scheduler/stop", methods=["POST"])
def api_scheduler_stop():
    global _scheduler_running
    _scheduler_running = False
    return jsonify({"status": "stopped"})


@app.route("/api/actions/discover", methods=["POST"])
def api_discover():
    try:
        import tm_client
        db.init_db()
        _run_task_bg("discovery", tm_client.discover_all)
        return jsonify({"status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/actions/snapshot", methods=["POST"])
def api_snapshot():
    try:
        import secondary_market as sm
        db.init_db()

        def _snapshot():
            all_events = db.get_tracked_events(status_filter="ON_SALE")

            # Prioritise events that have at least one real data point.
            # Events with neither capacity nor face value produce a uniform
            # £84/+110% estimate that is meaningless noise — skip them.
            with_data    = [e for e in all_events if e["capacity"] or e["face_value_min"]]
            without_data = [e for e in all_events if not e["capacity"] and not e["face_value_min"]]
            events = (with_data + without_data)[:20]

            logger.info(f"Snapshot: {len(with_data)} events with data, {len(without_data)} without; snapshotting {len(events)}")

            for event in events:
                try:
                    face_value = event["face_value_min"] or 40.0
                    sec = sm.get_secondary_data(
                        artist=event["artist"], city=event["city"],
                        event_date=event["event_date"], face_value=face_value,
                        demand_signals={"capacity": event["capacity"]},
                    )
                    if sec.low_ask > 0:
                        spread = (sec.low_ask / face_value) - 1
                        db.insert_snapshot(event["id"], {
                            "tm_status":          event["status"],
                            "secondary_source":   sec.source,
                            "secondary_low":      sec.low_ask,
                            "secondary_median":   sec.median_ask,
                            "secondary_high":     sec.high_ask,
                            "secondary_listings": sec.listings,
                            "spread_pct":         spread,
                            "net_profit_est":     sec.net_profit_est,
                        })
                except Exception as err:
                    logger.warning(f"Snapshot error {event['artist']}: {err}")

        _run_task_bg("snapshot", _snapshot)
        return jsonify({"status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/actions/restock", methods=["POST"])
def api_restock():
    try:
        import restock_monitor
        db.init_db()
        _run_task_bg("restock", restock_monitor.run_restock_check)
        return jsonify({"status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/actions/analyse", methods=["POST"])
def api_analyse():
    try:
        import analyzer
        db.init_db()

        def _analyse():
            events = db.get_events_needing_analysis(hours_since_last=1)
            if not events:
                logger.info("No events need analysis.")
                return
            event_ids = [e["id"] for e in events[:15]]
            results   = analyzer.analyse_batch(event_ids)
            import discord_alerts
            discord_alerts.send_track_alerts(results, scan_count=len(event_ids))

        _run_task_bg("analysis", _analyse)
        return jsonify({"status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/actions/analyse/<int:event_id>", methods=["POST"])
def api_analyse_single(event_id: int):
    """Analizira jedan specifični event."""
    try:
        import analyzer
        db.init_db()
        _run_task_bg("analysis", lambda: analyzer.analyse_batch([event_id]))
        return jsonify({"status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/discord/test", methods=["POST"])
def api_discord_test():
    try:
        import requests as req
        import importlib
        from dotenv import load_dotenv
        load_dotenv(override=True)
        importlib.reload(config)

        url = os.getenv("DISCORD_WEBHOOK_URL", "")
        if not url or "XXXXXXXXXX" in url:
            return jsonify({"error": "DISCORD_WEBHOOK_URL not set — paste your real webhook URL in Settings first"}), 400

        payload = {
            "username": "Entradas Ticket Scout",
            "embeds": [{
                "title": "✅ Entradas Ticket Scout — Connected",
                "description": (
                    "Your Discord webhook is working correctly.\n\n"
                    "You'll receive **TRACK alerts** here when profitable events are found, "
                    "and **restock notifications** when sold-out events go back on sale."
                ),
                "color": 0x00FF88,
                "fields": [
                    {"name": "Status", "value": "🟢 Live", "inline": True},
                    {"name": "Alerts", "value": "TRACK + Restocks", "inline": True},
                ],
                "footer": {"text": "Entradas Ticket Scout • EU/UK Resale Intelligence"},
            }]
        }
        r = req.post(url, json=payload, timeout=10)
        if r.status_code in (200, 204):
            return jsonify({"status": "ok"})
        else:
            return jsonify({"status": "error", "code": r.status_code, "error": r.text[:300]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config")
def api_config_get():
    """Vraća config (API ključevi maskirani)."""
    def mask(val: str) -> str:
        if not val or len(val) < 8:
            return "***"
        return val[:6] + "***" + val[-4:]

    return jsonify({
        "CLAUDE_MODEL":        config.CLAUDE_MODEL,
        "TM_COUNTRIES":        ",".join(config.TM_COUNTRIES),
        "TM_CATEGORIES":       ",".join(config.TM_CATEGORIES),
        "TM_MONTHS_AHEAD":     config.TM_MONTHS_AHEAD,
        "MIN_DEMAND_SCORE":    config.MIN_DEMAND_SCORE,
        "MIN_RESALE_POTENTIAL":config.MIN_RESALE_POTENTIAL,
        "MIN_SPREAD_PCT":      config.MIN_SPREAD_PCT,
        "MAX_VENUE_CAPACITY":  config.MAX_VENUE_CAPACITY,
        "MIN_VENUE_CAPACITY":  config.MIN_VENUE_CAPACITY,
        "FRICTION_RATE":       config.FRICTION_RATE,
        "INTERVAL_RESTOCK":    config.INTERVAL_RESTOCK,
        "INTERVAL_SNAPSHOT":   config.INTERVAL_SNAPSHOT,
        "ANTHROPIC_API_KEY":   mask(config.ANTHROPIC_API_KEY),
        "TM_API_KEY":          mask(config.TM_API_KEY),
        "DISCORD_WEBHOOK_URL":  mask(config.DISCORD_WEBHOOK_URL),
        "TELEGRAM_BOT_TOKEN":   mask(config.TELEGRAM_BOT_TOKEN),
        "TELEGRAM_CHAT_ID":     config.TELEGRAM_CHAT_ID,
        "ENABLE_TELEGRAM":      str(config.ENABLE_TELEGRAM).lower(),
        "DAILY_DIGEST_HOUR":    config.DAILY_DIGEST_HOUR,
    })


@app.route("/api/config", methods=["POST"])
def api_config_update():
    """Ažurira .env fajl s novim vrijednostima."""
    data = request.get_json() or {}
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

    # Pročitaj postojeći .env
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()

    # Ažuriraj ili dodaj vrijednosti
    safe_keys = {
        "CLAUDE_MODEL", "TM_COUNTRIES", "TM_CATEGORIES", "TM_MONTHS_AHEAD",
        "MIN_DEMAND_SCORE", "MIN_RESALE_POTENTIAL", "MIN_SPREAD_PCT",
        "MAX_VENUE_CAPACITY", "MIN_VENUE_CAPACITY", "FRICTION_RATE",
        "INTERVAL_RESTOCK", "INTERVAL_SNAPSHOT",
    }
    # Allow API key updates only if they look real (not masked)
    for key in ["ANTHROPIC_API_KEY", "TM_API_KEY", "DISCORD_WEBHOOK_URL",
                "DISCORD_RESTOCK_WEBHOOK_URL", "DISCORD_ROLE_ID",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "ENABLE_TELEGRAM", "DAILY_DIGEST_HOUR"]:
        if key in data and "***" not in str(data[key]):
            safe_keys.add(key)

    updated = {}
    for key, val in data.items():
        if key not in safe_keys:
            continue
        if "***" in str(val):
            continue  # Maskirane vrijednosti ne mijenjamo

        updated[key] = val
        found = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={val}\n"
                found = True
                break
        if not found:
            lines.append(f"{key}={val}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)

    # Reload env vars AND the config module so in-memory values update immediately
    from dotenv import load_dotenv
    import importlib
    load_dotenv(dotenv_path=env_path, override=True)
    importlib.reload(config)
    try:
        import discord_alerts
        importlib.reload(discord_alerts)
    except Exception:
        pass

    return jsonify({"status": "saved", "updated": list(updated.keys())})


@app.route("/api/signup/start", methods=["POST"])
def api_signup_start():
    """Start the signup bot with config from the UI."""
    import signup_runner
    data = request.get_json() or {}

    url     = data.get("url", "").strip()
    emails  = [e.strip() for e in data.get("emails", "").splitlines() if e.strip()]
    proxies = [p.strip() for p in data.get("proxies", "").splitlines() if p.strip()]

    if not url:
        return jsonify({"error": "Signup URL is required"}), 400
    if not emails:
        return jsonify({"error": "Email list is empty"}), 400

    use_playwright = data.get("use_playwright", False)

    cfg = {
        "url":             url,
        "emails":          emails,
        "proxies":         proxies,
        "threads":         int(data.get("threads", 5 if use_playwright else 50)),
        "country":         data.get("country", ""),
        "twocaptcha_key":  data.get("twocaptcha_key", ""),
        "discord_webhook": os.getenv("DISCORD_WEBHOOK_URL", ""),
    }

    if use_playwright:
        import signup_playwright
        runner = signup_playwright.PlaywrightSignupRunner(cfg)
        runner.start()
        # Store in signup_runner module so status/logs endpoints can find it
        import signup_runner as sr
        sr._current_runner = runner
    else:
        runner = signup_runner.start_runner(cfg)

    mode = "Playwright (browser)" if use_playwright else "HTTP (fast)"
    logger.info(f"Signup bot started [{mode}]: {len(emails)} emails, {len(proxies)} proxies, {cfg['threads']} threads")
    return jsonify({"status": "started", "total": len(emails), "mode": mode})


@app.route("/api/signup/stop", methods=["POST"])
def api_signup_stop():
    import signup_runner
    signup_runner.stop_runner()
    return jsonify({"status": "stopped"})


@app.route("/api/signup/status")
def api_signup_status():
    import signup_runner
    runner = signup_runner.get_runner()
    if not runner:
        return jsonify({"running": False, "total": 0, "success": 0, "failed": 0, "error": 0, "done": 0})
    return jsonify(runner.get_status())


@app.route("/api/signup/logs/stream")
def api_signup_logs_stream():
    """SSE stream of live signup logs — serves both signup_runner and script_runner output."""
    import signup_runner, script_runner

    def generate():
        yield 'data: {"msg": "Connected to log stream", "level": "info", "ts": "now"}\n\n'
        while True:
            # Check script_runner queue first (non-blocking)
            try:
                entry = script_runner.log_queue.get_nowait()
                yield f"data: {json.dumps(entry)}\n\n"
                continue
            except queue.Empty:
                pass
            # Then check signup_runner
            runner = signup_runner.get_runner()
            if runner:
                try:
                    entry = runner.log_queue.get(timeout=1)
                    yield f"data: {json.dumps(entry)}\n\n"
                    continue
                except queue.Empty:
                    pass
            time.sleep(0.5)
            yield ": heartbeat\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Script Runner ─────────────────────────────────────────────────────────────

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")


@app.route("/api/scripts", methods=["GET"])
def api_list_scripts():
    """List all saved .py scripts."""
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    scripts = []
    for fname in sorted(os.listdir(SCRIPTS_DIR)):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(SCRIPTS_DIR, fname)
        stat  = os.stat(fpath)
        scripts.append({
            "name":     fname,
            "size":     stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    import script_runner as sr
    status = sr.get_status()
    return jsonify({"scripts": scripts, "running": status["running"], "running_script": status["script"]})


@app.route("/api/scripts/upload", methods=["POST"])
def api_upload_script():
    """Save an uploaded .py file to the scripts directory."""
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file provided"}), 400
    fname = f.filename or "script.py"
    # Sanitise filename — allow only safe characters
    import re as _re
    fname = _re.sub(r"[^\w\-. ]", "_", fname)
    if not fname.endswith(".py"):
        fname += ".py"
    dest = os.path.join(SCRIPTS_DIR, fname)
    f.save(dest)
    logger.info(f"Script uploaded: {fname} ({os.path.getsize(dest)} bytes)")
    return jsonify({"status": "saved", "name": fname})


@app.route("/api/scripts/<path:name>", methods=["DELETE"])
def api_delete_script(name):
    """Delete a script file."""
    fpath = os.path.join(SCRIPTS_DIR, os.path.basename(name))
    if not os.path.isfile(fpath):
        return jsonify({"error": "Not found"}), 404
    os.unlink(fpath)
    return jsonify({"status": "deleted"})


@app.route("/api/scripts/<path:name>/run", methods=["POST"])
def api_run_script(name):
    """Run a script with the provided config."""
    import script_runner as sr
    import database as db_mod

    fpath = os.path.join(SCRIPTS_DIR, os.path.basename(name))
    if not os.path.isfile(fpath):
        return jsonify({"error": f"Script not found: {name}"}), 404

    data       = request.get_json() or {}
    url        = (data.get("url") or "").strip()
    proxy_text = (data.get("proxies") or "")
    account_ids = data.get("account_ids", [])   # list of account IDs from DB

    if not url:
        return jsonify({"error": "URL is required"}), 400

    # Build accounts list: email:password lines from DB
    accounts_lines = []
    if account_ids:
        db_mod.init_db()
        for acc_id in account_ids:
            acc = db_mod.get_account(int(acc_id))
            if acc:
                email = acc.get("email", "")
                pwd   = acc.get("password", "")
                if email:
                    accounts_lines.append(f"{email}:{pwd}" if pwd else email)
    else:
        # fallback: plain email list from payload
        raw_emails = data.get("emails", "")
        accounts_lines = [e.strip() for e in raw_emails.splitlines() if e.strip()]

    if not accounts_lines:
        return jsonify({"error": "No accounts selected"}), 400

    proxy_lines = [p.strip() for p in proxy_text.splitlines() if p.strip()]

    try:
        result = sr.run_script(fpath, url, accounts_lines, proxy_lines)
        return jsonify(result)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409


@app.route("/api/scripts/status", methods=["GET"])
def api_script_status():
    import script_runner as sr
    return jsonify(sr.get_status())


@app.route("/api/scripts/stop", methods=["POST"])
def api_stop_script():
    import script_runner as sr
    sr.stop_script()
    return jsonify({"status": "stopped"})


# ── Dashboard Intelligence ─────────────────────────────────────────────────────

@app.route("/api/dashboard/top-opportunities")
def api_top_opportunities():
    try:
        db.init_db()
        rows = db.get_top_opportunities(limit=5)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/dashboard/country-stats")
def api_country_stats():
    try:
        db.init_db()
        rows = db.get_country_stats()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/dashboard/profit-potential")
def api_profit_potential():
    try:
        db.init_db()
        total = db.get_total_profit_potential()
        return jsonify({"total": total})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Alert History ──────────────────────────────────────────────────────────────

@app.route("/api/alert-history")
def api_alert_history():
    try:
        db.init_db()
        alert_type = request.args.get("type", "")
        platform   = request.args.get("platform", "")
        limit      = int(request.args.get("limit", 100))
        rows = db.list_alert_history(limit=limit, alert_type=alert_type,
                                     platform=platform)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Watchlist ──────────────────────────────────────────────────────────────────

@app.route("/api/watchlist")
def api_watchlist_get():
    try:
        db.init_db()
        rows = db.get_watchlist()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/watchlist/<int:event_id>", methods=["POST"])
def api_watchlist_add(event_id: int):
    try:
        db.init_db()
        data = request.get_json() or {}
        db.add_to_watchlist(event_id, notes=data.get("notes", ""))
        return jsonify({"status": "added"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/watchlist/<int:event_id>", methods=["DELETE"])
def api_watchlist_remove(event_id: int):
    try:
        db.init_db()
        db.remove_from_watchlist(event_id)
        return jsonify({"status": "removed"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Notifications ──────────────────────────────────────────────────────────────

@app.route("/api/actions/daily-digest", methods=["POST"])
def api_daily_digest():
    try:
        db.init_db()
        import discord_alerts
        stats = db.get_stats()
        try:
            stats["profit_potential"] = db.get_total_profit_potential()
        except Exception:
            stats["profit_potential"] = 0
        discord_alerts.send_daily_digest(stats)
        try:
            import telegram_alerts
            telegram_alerts.send_daily_digest(stats)
        except Exception:
            pass
        return jsonify({"status": "sent"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/telegram/test", methods=["POST"])
def api_telegram_test():
    try:
        import telegram_alerts
        ok = telegram_alerts.test_connection()
        if ok:
            return jsonify({"status": "ok"})
        return jsonify({"error": "Failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/countries")
def api_countries():
    """Vraća listu jedinstvenih država u DB."""
    try:
        db.init_db()
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT country FROM events ORDER BY country"
            ).fetchall()
        return jsonify([r["country"] for r in rows])
    except Exception as e:
        return jsonify([])


@app.route("/api/logs/stream")
def api_logs_stream():
    """Server-Sent Events stream za live logove."""
    def generate():
        yield "data: {\"msg\": \"Connected to log stream\", \"level\": \"info\", \"ts\": \"now\"}\n\n"
        while True:
            try:
                entry = _log_queue.get(timeout=30)
                yield f"data: {json.dumps(entry)}\n\n"
            except queue.Empty:
                yield ": heartbeat\n\n"  # keep-alive

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found", "path": request.path}), 404
    return render_template("error.html", code=404,
                           title="Page Not Found",
                           message="The page you're looking for doesn't exist."), 404


@app.errorhandler(500)
def server_error(e):
    logger.error(f"Internal server error: {e}", exc_info=True)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return render_template("error.html", code=500,
                           title="Server Error",
                           message="Something went wrong. Check the server logs."), 500


@app.errorhandler(403)
def forbidden(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Forbidden"}), 403
    return render_template("error.html", code=403,
                           title="Access Denied",
                           message="You don't have permission to access this page."), 403


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    db.init_db()
    _auth.ensure_default_admin()
    logger.info(f"TicketScout UI starting on http://{args.host}:{args.port}")

    app.run(host=args.host, port=args.port, debug=args.debug,
            threaded=True, use_reloader=False)
