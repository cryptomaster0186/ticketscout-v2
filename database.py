"""
database.py — SQLite layer.

Tabele:
  events          — master lista svih eventa
  snapshots       — periodični price/availability checkovi
  analysis        — Claude analiza po eventu
  restock_alerts  — detektovani restockovi
"""

import sqlite3
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, List, Dict

import config

logger = logging.getLogger(__name__)

# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tm_id           TEXT UNIQUE,
    artist          TEXT NOT NULL,
    city            TEXT NOT NULL,
    country         TEXT NOT NULL,
    event_date      TEXT NOT NULL,
    venue           TEXT,
    capacity        INTEGER,
    face_value_min  REAL,
    face_value_max  REAL,
    currency        TEXT DEFAULT 'GBP',
    tm_url          TEXT,
    category        TEXT,
    status          TEXT DEFAULT 'ON_SALE',
    first_seen      TEXT,
    last_updated    TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id             INTEGER NOT NULL,
    ts                   TEXT NOT NULL,
    tm_status            TEXT,
    secondary_source     TEXT,
    secondary_low        REAL,
    secondary_median     REAL,
    secondary_high       REAL,
    secondary_listings   INTEGER,
    spread_pct           REAL,
    net_profit_est       REAL,
    FOREIGN KEY (event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS analysis (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         INTEGER NOT NULL,
    ts               TEXT NOT NULL,
    verdict          TEXT,
    demand_score     INTEGER,
    resale_potential TEXT,
    sellout_eta      TEXT,
    velocity_trend   TEXT,
    reason           TEXT,
    FOREIGN KEY (event_id) REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS restock_alerts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         INTEGER NOT NULL,
    detected_at      TEXT NOT NULL,
    prev_status      TEXT,
    new_status       TEXT,
    tickets_appeared INTEGER,
    discord_sent     INTEGER DEFAULT 0,
    FOREIGN KEY (event_id) REFERENCES events(id)
);

CREATE INDEX IF NOT EXISTS idx_events_tm_id    ON events(tm_id);
CREATE INDEX IF NOT EXISTS idx_events_status   ON events(status);
CREATE INDEX IF NOT EXISTS idx_snapshots_event ON snapshots(event_id, ts);
CREATE INDEX IF NOT EXISTS idx_analysis_event  ON analysis(event_id);
CREATE INDEX IF NOT EXISTS idx_restock_event   ON restock_alerts(event_id);

-- ── Accounts Database ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_name TEXT,
    email        TEXT NOT NULL,
    password     TEXT,
    first_name   TEXT,
    last_name    TEXT,
    phone        TEXT,
    address1     TEXT,
    address2     TEXT,
    city         TEXT,
    postcode     TEXT,
    country      TEXT DEFAULT 'GB',
    region       TEXT,
    proxy        TEXT,
    imap_email   TEXT,
    imap_password TEXT,
    imap_server  TEXT,
    notes        TEXT,
    status       TEXT DEFAULT 'active',
    group_tag    TEXT,
    health       TEXT DEFAULT 'fresh',
    tags         TEXT DEFAULT '[]',
    created_at   TEXT,
    updated_at   TEXT
);

-- ── Proxies ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS proxies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy_url   TEXT NOT NULL,
    host        TEXT,
    port        TEXT,
    username    TEXT,
    password    TEXT,
    proxy_type  TEXT DEFAULT 'http',
    label       TEXT,
    status      TEXT DEFAULT 'untested',
    last_tested TEXT,
    notes       TEXT,
    created_at  TEXT
);

-- ── Batch Templates ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS batch_templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT
);

-- ── Export History ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS export_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER,
    batch_name  TEXT,
    row_count   INTEGER,
    exported_at TEXT
);

-- ── Signup History ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signup_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL,
    event_url   TEXT,
    status      TEXT DEFAULT 'success',
    notes       TEXT,
    created_at  TEXT,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_proxies_status     ON proxies(status);
CREATE INDEX IF NOT EXISTS idx_export_history_bid ON export_history(batch_id);
CREATE INDEX IF NOT EXISTS idx_signup_history_aid ON signup_history(account_id);

CREATE TABLE IF NOT EXISTS csv_batches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_name   TEXT NOT NULL,
    target_url   TEXT,
    mode         TEXT DEFAULT 'buy',
    use_proxy    TEXT DEFAULT '0',
    number_of_tickets  INTEGER DEFAULT 2,
    max_tickets  INTEGER,
    min_price    REAL,
    max_price    REAL,
    include_resale TEXT DEFAULT '0',
    ticket_type_priority TEXT,
    delay_between_accounts INTEGER DEFAULT 0,
    presale_code TEXT,
    wait_queue   TEXT DEFAULT '0',
    sections     TEXT,
    aco_profile  TEXT,
    monitor_wait_time INTEGER,
    message      TEXT,
    otp_provider TEXT,
    notes        TEXT,
    created_at   TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS batch_accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id     INTEGER NOT NULL,
    account_id   INTEGER NOT NULL,
    sort_order   INTEGER DEFAULT 0,
    row_overrides TEXT DEFAULT '{}',
    FOREIGN KEY (batch_id)   REFERENCES csv_batches(id) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE INDEX IF NOT EXISTS idx_accounts_status    ON accounts(status);
CREATE INDEX IF NOT EXISTS idx_accounts_group     ON accounts(group_tag);
CREATE INDEX IF NOT EXISTS idx_accounts_email     ON accounts(email);
CREATE INDEX IF NOT EXISTS idx_batch_accounts_bid ON batch_accounts(batch_id);

-- ── Alert History ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alert_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type   TEXT NOT NULL,
    event_id     INTEGER,
    artist       TEXT,
    city         TEXT,
    country      TEXT,
    event_date   TEXT,
    demand_score INTEGER,
    net_profit   REAL,
    platform     TEXT DEFAULT 'discord',
    sent_at      TEXT NOT NULL,
    details_json TEXT DEFAULT '{}',
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE SET NULL
);

-- ── Watchlist ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlist (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id  INTEGER NOT NULL UNIQUE,
    added_at  TEXT NOT NULL,
    notes     TEXT,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_alert_history_ts   ON alert_history(sent_at);
CREATE INDEX IF NOT EXISTS idx_alert_history_type ON alert_history(alert_type);
CREATE INDEX IF NOT EXISTS idx_watchlist_event    ON watchlist(event_id);

-- ── Users (auth) ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT DEFAULT 'user',
    created_at    TEXT,
    last_login    TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
"""

# ── Connection ─────────────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Inicijalizira bazu — pokreni jednom pri startu."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    _migrate_db()
    logger.info(f"Database initialised at {config.DB_PATH}")


def _migrate_db():
    """Add new columns to existing databases safely."""
    migrations = [
        ("accounts", "health",    "TEXT DEFAULT 'fresh'"),
        ("accounts", "tags",      "TEXT DEFAULT '[]'"),
        ("events",   "dismissed", "INTEGER DEFAULT 0"),
    ]
    with get_conn() as conn:
        for table, col, col_def in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
            except Exception:
                pass  # column already exists — skip


# ── Events ─────────────────────────────────────────────────────────────────────

def upsert_event(e: dict) -> int:
    """
    Unos ili update eventa. Vraća id.
    Potrebni ključevi: tm_id, artist, city, country, event_date.
    """
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM events WHERE tm_id = ?", (e["tm_id"],)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE events SET
                    face_value_min = COALESCE(?, face_value_min),
                    face_value_max = COALESCE(?, face_value_max),
                    capacity       = COALESCE(?, capacity),
                    venue          = COALESCE(NULLIF(?, ''), venue),
                    status         = ?,
                    last_updated   = ?
                WHERE tm_id = ?
            """, (
                e.get("face_value_min"),
                e.get("face_value_max"),
                e.get("capacity") or None,
                e.get("venue") or None,
                e.get("status", "ON_SALE"),
                now,
                e["tm_id"],
            ))
            return existing["id"]
        else:
            cur = conn.execute("""
                INSERT INTO events (tm_id, artist, city, country, event_date,
                    venue, capacity, face_value_min, face_value_max, currency,
                    tm_url, category, status, first_seen, last_updated)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                e["tm_id"], e["artist"], e["city"], e["country"], e["event_date"],
                e.get("venue"), e.get("capacity"), e.get("face_value_min"),
                e.get("face_value_max"), e.get("currency", "GBP"),
                e.get("tm_url"), e.get("category", "music"),
                e.get("status", "ON_SALE"), now, now,
            ))
            return cur.lastrowid


def get_tracked_events(status_filter: Optional[str] = None) -> List[sqlite3.Row]:
    """Vraća evente za praćenje."""
    with get_conn() as conn:
        if status_filter:
            return conn.execute(
                "SELECT * FROM events WHERE status = ? ORDER BY event_date",
                (status_filter,)
            ).fetchall()
        return conn.execute(
            "SELECT * FROM events WHERE status != 'CANCELLED' ORDER BY event_date"
        ).fetchall()


def update_event_status(event_id: int, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET status=?, last_updated=? WHERE id=?",
            (status, datetime.utcnow().isoformat(), event_id)
        )


def get_event_by_id(event_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()


def dismiss_event(event_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET dismissed=1, last_updated=? WHERE id=?",
            (datetime.utcnow().isoformat(), event_id)
        )


def undismiss_event(event_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET dismissed=0, last_updated=? WHERE id=?",
            (datetime.utcnow().isoformat(), event_id)
        )


def get_sidebar_stats() -> dict:
    """Quick aggregate counts for sidebar badges."""
    with get_conn() as conn:
        watchlist_count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        dismissed_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE dismissed=1"
        ).fetchone()[0]
    return {
        "watchlist":  watchlist_count,
        "dismissed":  dismissed_count,
    }


# ── Snapshots ──────────────────────────────────────────────────────────────────

def insert_snapshot(event_id: int, data: dict):
    """Sprema jedan price/availability snapshot."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO snapshots (event_id, ts, tm_status, secondary_source,
                secondary_low, secondary_median, secondary_high, secondary_listings,
                spread_pct, net_profit_est)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            event_id,
            datetime.utcnow().isoformat(),
            data.get("tm_status"),
            data.get("secondary_source"),
            data.get("secondary_low"),
            data.get("secondary_median"),
            data.get("secondary_high"),
            data.get("secondary_listings"),
            data.get("spread_pct"),
            data.get("net_profit_est"),
        ))


def get_recent_snapshots(event_id: int, limit: int = 20) -> List[sqlite3.Row]:
    """Vraća N najnovijih snapshotova za event (za velocity analizu)."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM snapshots
            WHERE event_id = ?
            ORDER BY ts DESC
            LIMIT ?
        """, (event_id, limit)).fetchall()


def get_last_snapshot(event_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM snapshots WHERE event_id = ? ORDER BY ts DESC LIMIT 1
        """, (event_id,)).fetchone()


# ── Analysis ───────────────────────────────────────────────────────────────────

def insert_analysis(event_id: int, data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO analysis (event_id, ts, verdict, demand_score,
                resale_potential, sellout_eta, velocity_trend, reason)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            event_id,
            datetime.utcnow().isoformat(),
            data.get("verdict"),
            data.get("demand_score"),
            data.get("resale_potential"),
            data.get("sellout_eta"),
            data.get("velocity_trend"),
            data.get("reason"),
        ))


def get_latest_analysis(event_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM analysis WHERE event_id = ? ORDER BY ts DESC LIMIT 1
        """, (event_id,)).fetchone()


def get_events_needing_analysis(hours_since_last: int = 6) -> List[sqlite3.Row]:
    """
    Vraća evente kojima ili nikad nije rađena analiza,
    ili je posljednja analiza starija od N sati.
    """
    with get_conn() as conn:
        return conn.execute("""
            SELECT e.*
            FROM events e
            LEFT JOIN (
                SELECT event_id, MAX(ts) AS latest_ts FROM analysis GROUP BY event_id
            ) a ON e.id = a.event_id
            WHERE e.status = 'ON_SALE'
              AND (a.latest_ts IS NULL
                   OR datetime(a.latest_ts) < datetime('now', ? || ' hours'))
            ORDER BY e.event_date
        """, (f"-{hours_since_last}",)).fetchall()


# ── Restock alerts ─────────────────────────────────────────────────────────────

def insert_restock_alert(event_id: int, prev_status: str, new_status: str,
                          tickets_appeared: Optional[int] = None):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO restock_alerts
                (event_id, detected_at, prev_status, new_status, tickets_appeared)
            VALUES (?,?,?,?,?)
        """, (
            event_id,
            datetime.utcnow().isoformat(),
            prev_status,
            new_status,
            tickets_appeared,
        ))


def get_unsent_restock_alerts() -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("""
            SELECT r.*, e.artist, e.city, e.country, e.event_date,
                   e.venue, e.tm_url, e.face_value_min, e.face_value_max
            FROM restock_alerts r
            JOIN events e ON r.event_id = e.id
            WHERE r.discord_sent = 0
            ORDER BY r.detected_at
        """).fetchall()


def mark_restock_sent(alert_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE restock_alerts SET discord_sent=1 WHERE id=?", (alert_id,)
        )


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    with get_conn() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        on_sale  = conn.execute("SELECT COUNT(*) FROM events WHERE status='ON_SALE'").fetchone()[0]
        sold_out = conn.execute("SELECT COUNT(*) FROM events WHERE status='SOLD_OUT'").fetchone()[0]
        tracked  = conn.execute("SELECT COUNT(*) FROM analysis WHERE verdict='TRACK'").fetchone()[0]
        restocks = conn.execute("SELECT COUNT(*) FROM restock_alerts").fetchone()[0]
        return {
            "total_events": total,
            "on_sale": on_sale,
            "sold_out": sold_out,
            "tracked": tracked,
            "restocks_detected": restocks,
        }


# ── Accounts ──────────────────────────────────────────────────────────────────

_ACCOUNT_FIELDS = [
    "account_name", "email", "password", "first_name", "last_name",
    "phone", "address1", "address2", "city", "postcode", "country", "region",
    "proxy", "imap_email", "imap_password", "imap_server",
    "notes", "status", "group_tag", "health", "tags",
]


def create_account(data: dict) -> int:
    now = datetime.utcnow().isoformat()
    cols = _ACCOUNT_FIELDS + ["created_at", "updated_at"]
    vals = [data.get(f) for f in _ACCOUNT_FIELDS] + [now, now]
    placeholders = ",".join(["?"] * len(cols))
    col_sql = ",".join(cols)
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO accounts ({col_sql}) VALUES ({placeholders})", vals
        )
        return cur.lastrowid


def update_account(account_id: int, data: dict):
    now = datetime.utcnow().isoformat()
    sets = ", ".join(f"{f} = ?" for f in _ACCOUNT_FIELDS) + ", updated_at = ?"
    vals = [data.get(f) for f in _ACCOUNT_FIELDS] + [now, account_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE accounts SET {sets} WHERE id = ?", vals)


def get_account(account_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


def delete_account(account_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def duplicate_account(account_id: int) -> int:
    acc = get_account(account_id)
    if not acc:
        raise ValueError(f"Account {account_id} not found")
    data = dict(acc)
    data.pop("id", None)
    data.pop("created_at", None)
    data.pop("updated_at", None)
    original_name = data.get("account_name") or data.get("email", "")
    data["account_name"] = f"{original_name} (copy)"
    return create_account(data)


def list_accounts(
    q: str = "",
    status: str = "",
    health: str = "",
    group_tag: str = "",
    country: str = "",
    page: int = 1,
    per_page: int = 100,
) -> dict:
    where = ["1=1"]
    params: list = []

    if q:
        where.append(
            "(LOWER(email) LIKE ? OR LOWER(first_name) LIKE ? OR LOWER(last_name) LIKE ?"
            " OR LOWER(account_name) LIKE ? OR LOWER(notes) LIKE ?)"
        )
        like = f"%{q.lower()}%"
        params += [like, like, like, like, like]
    if status:
        where.append("status = ?")
        params.append(status)
    if health:
        where.append("health = ?")
        params.append(health)
    if group_tag:
        where.append("group_tag = ?")
        params.append(group_tag)
    if country:
        where.append("country = ?")
        params.append(country)

    where_sql = " AND ".join(where)
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM accounts WHERE {where_sql}", params
        ).fetchone()[0]
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"SELECT * FROM accounts WHERE {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
    return {"total": total, "page": page, "per_page": per_page, "accounts": [dict(r) for r in rows]}


def get_account_groups() -> List[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT group_tag FROM accounts WHERE group_tag IS NOT NULL AND group_tag != '' ORDER BY group_tag"
        ).fetchall()
    return [r[0] for r in rows]


def get_account_countries() -> List[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT country FROM accounts WHERE country IS NOT NULL AND country != '' ORDER BY country"
        ).fetchall()
    return [r[0] for r in rows]


# ── CSV Batches ───────────────────────────────────────────────────────────────

_BATCH_FIELDS = [
    "batch_name", "target_url", "mode", "use_proxy", "number_of_tickets",
    "max_tickets", "min_price", "max_price", "include_resale",
    "ticket_type_priority", "delay_between_accounts", "presale_code",
    "wait_queue", "sections", "aco_profile", "monitor_wait_time",
    "message", "otp_provider", "notes",
]


def create_batch(data: dict) -> int:
    now = datetime.utcnow().isoformat()
    cols = _BATCH_FIELDS + ["created_at", "updated_at"]
    vals = [data.get(f) for f in _BATCH_FIELDS] + [now, now]
    placeholders = ",".join(["?"] * len(cols))
    col_sql = ",".join(cols)
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO csv_batches ({col_sql}) VALUES ({placeholders})", vals
        )
        return cur.lastrowid


def update_batch(batch_id: int, data: dict):
    now = datetime.utcnow().isoformat()
    sets = ", ".join(f"{f} = ?" for f in _BATCH_FIELDS) + ", updated_at = ?"
    vals = [data.get(f) for f in _BATCH_FIELDS] + [now, batch_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE csv_batches SET {sets} WHERE id = ?", vals)


def get_batch(batch_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM csv_batches WHERE id = ?", (batch_id,)).fetchone()


def delete_batch(batch_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM batch_accounts WHERE batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM csv_batches WHERE id = ?", (batch_id,))


def list_batches() -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT b.*, COUNT(ba.id) AS account_count FROM csv_batches b "
            "LEFT JOIN batch_accounts ba ON ba.batch_id = b.id "
            "GROUP BY b.id ORDER BY b.id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def set_batch_accounts(batch_id: int, account_ids: List[int]):
    """Replace all accounts for a batch."""
    with get_conn() as conn:
        conn.execute("DELETE FROM batch_accounts WHERE batch_id = ?", (batch_id,))
        for i, acc_id in enumerate(account_ids):
            conn.execute(
                "INSERT INTO batch_accounts (batch_id, account_id, sort_order) VALUES (?,?,?)",
                (batch_id, acc_id, i),
            )


def get_batch_rows(batch_id: int) -> List[dict]:
    """Return batch + each account's data joined together, ready for CSV generation."""
    batch = get_batch(batch_id)
    if not batch:
        return []
    batch_dict = dict(batch)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.*, ba.row_overrides, ba.sort_order FROM batch_accounts ba "
            "JOIN accounts a ON a.id = ba.account_id "
            "WHERE ba.batch_id = ? ORDER BY ba.sort_order, ba.id",
            (batch_id,),
        ).fetchall()

    result = []
    for i, row in enumerate(rows):
        acc = dict(row)
        overrides = json.loads(acc.pop("row_overrides", "{}") or "{}")
        result.append({
            "batch":     batch_dict,
            "account":   acc,
            "overrides": overrides,
            "row_index": i,
        })
    return result


def save_batch_row_override(batch_id: int, account_id: int, overrides: dict):
    with get_conn() as conn:
        conn.execute(
            "UPDATE batch_accounts SET row_overrides = ? WHERE batch_id = ? AND account_id = ?",
            (json.dumps(overrides), batch_id, account_id),
        )


def clone_batch(batch_id: int) -> int:
    """Duplicate a batch (config + account list) and return the new batch id."""
    batch = get_batch(batch_id)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found")
    data = dict(batch)
    data.pop("id", None)
    data.pop("created_at", None)
    data.pop("updated_at", None)
    data["batch_name"] = data.get("batch_name", "batch") + " (copy)"
    new_id = create_batch(data)
    # Copy account assignments
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT account_id, sort_order, row_overrides FROM batch_accounts WHERE batch_id = ? ORDER BY sort_order",
            (batch_id,),
        ).fetchall()
        for r in rows:
            conn.execute(
                "INSERT INTO batch_accounts (batch_id, account_id, sort_order, row_overrides) VALUES (?,?,?,?)",
                (new_id, r["account_id"], r["sort_order"], r["row_overrides"]),
            )
    return new_id


# ── Proxies ────────────────────────────────────────────────────────────────────

_PROXY_FIELDS = ["proxy_url", "host", "port", "username", "password",
                 "proxy_type", "label", "status", "notes"]


def create_proxy(data: dict) -> int:
    now = datetime.utcnow().isoformat()
    cols = _PROXY_FIELDS + ["created_at"]
    vals = [data.get(f) for f in _PROXY_FIELDS] + [now]
    ph   = ",".join(["?"] * len(cols))
    with get_conn() as conn:
        cur = conn.execute(f"INSERT INTO proxies ({','.join(cols)}) VALUES ({ph})", vals)
        return cur.lastrowid


def update_proxy(proxy_id: int, data: dict):
    sets = ", ".join(f"{f} = ?" for f in _PROXY_FIELDS)
    vals = [data.get(f) for f in _PROXY_FIELDS] + [proxy_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE proxies SET {sets} WHERE id = ?", vals)


def get_proxy(proxy_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM proxies WHERE id = ?", (proxy_id,)).fetchone()


def delete_proxy(proxy_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))


def list_proxies(status: str = "") -> List[dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute("SELECT * FROM proxies WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM proxies ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def set_proxy_status(proxy_id: int, status: str):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute("UPDATE proxies SET status = ?, last_tested = ? WHERE id = ?", (status, now, proxy_id))


# ── Batch Templates ────────────────────────────────────────────────────────────

def create_template(name: str, config: dict) -> int:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO batch_templates (name, config_json, created_at) VALUES (?,?,?)",
            (name, json.dumps(config), now),
        )
        return cur.lastrowid


def list_templates() -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM batch_templates ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def get_template(tpl_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM batch_templates WHERE id = ?", (tpl_id,)).fetchone()


def delete_template(tpl_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM batch_templates WHERE id = ?", (tpl_id,))


# ── Export History ─────────────────────────────────────────────────────────────

def log_export(batch_id: Optional[int], batch_name: str, row_count: int):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO export_history (batch_id, batch_name, row_count, exported_at) VALUES (?,?,?,?)",
            (batch_id, batch_name, row_count, now),
        )


def list_exports(limit: int = 50) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM export_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Signup History ─────────────────────────────────────────────────────────────

def log_signup(account_id: int, event_url: str, status: str, notes: str = ""):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO signup_history (account_id, event_url, status, notes, created_at) VALUES (?,?,?,?,?)",
            (account_id, event_url, status, notes, now),
        )


def get_account_signup_history(account_id: int, limit: int = 50) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM signup_history WHERE account_id = ? ORDER BY id DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def bulk_log_signups(results: List[dict]):
    """
    results: [{account_id, event_url, status, notes}, ...]
    Also updates account health based on result:
      success → health='used', error/ban → health='flagged'
    """
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        for r in results:
            acc_id = r.get("account_id")
            if not acc_id:
                continue
            conn.execute(
                "INSERT INTO signup_history (account_id, event_url, status, notes, created_at) VALUES (?,?,?,?,?)",
                (acc_id, r.get("event_url", ""), r.get("status", "success"), r.get("notes", ""), now),
            )
            # Update health
            status = r.get("status", "")
            if status == "success":
                conn.execute("UPDATE accounts SET health = 'used', updated_at = ? WHERE id = ?", (now, acc_id))
            elif status in ("ban", "blocked"):
                conn.execute("UPDATE accounts SET health = 'banned', updated_at = ? WHERE id = ?", (now, acc_id))
            elif status == "error":
                conn.execute("UPDATE accounts SET health = 'flagged', updated_at = ? WHERE id = ?", (now, acc_id))


# ── Alert History ──────────────────────────────────────────────────────────────

def log_alert(alert_type: str, event_id=None, artist="", city="", country="",
              event_date="", demand_score=None, net_profit=None,
              platform="discord", details: dict = None):
    """Log a sent alert to alert_history."""
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO alert_history
                (alert_type, event_id, artist, city, country, event_date,
                 demand_score, net_profit, platform, sent_at, details_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (alert_type, event_id, artist, city, country, event_date,
              demand_score, net_profit, platform, now,
              json.dumps(details or {})))


def list_alert_history(limit: int = 100, alert_type: str = "",
                       platform: str = "") -> List:
    with get_conn() as conn:
        where = ["1=1"]
        params: list = []
        if alert_type:
            where.append("alert_type = ?")
            params.append(alert_type)
        if platform:
            where.append("platform = ?")
            params.append(platform)
        rows = conn.execute(
            f"SELECT * FROM alert_history WHERE {' AND '.join(where)} "
            f"ORDER BY sent_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    return rows


# ── Watchlist ──────────────────────────────────────────────────────────────────

def add_to_watchlist(event_id: int, notes: str = "") -> bool:
    now = datetime.utcnow().isoformat()
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (event_id, added_at, notes) VALUES (?,?,?)",
                (event_id, now, notes),
            )
        return True
    except Exception:
        return False


def remove_from_watchlist(event_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE event_id = ?", (event_id,))


def get_watchlist() -> List:
    with get_conn() as conn:
        return conn.execute("""
            SELECT w.*, e.artist, e.city, e.country, e.event_date,
                   e.venue, e.tm_url, e.status, e.face_value_min,
                   a.demand_score, a.resale_potential, a.sellout_eta
            FROM watchlist w
            JOIN events e ON w.event_id = e.id
            LEFT JOIN (
                SELECT event_id, demand_score, resale_potential, sellout_eta
                FROM analysis ORDER BY ts DESC
            ) a ON a.event_id = e.id
            ORDER BY w.added_at DESC
        """).fetchall()


def get_watchlist_ids() -> set:
    with get_conn() as conn:
        rows = conn.execute("SELECT event_id FROM watchlist").fetchall()
    return {r["event_id"] for r in rows}


def is_watchlisted(event_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM watchlist WHERE event_id = ?", (event_id,)
        ).fetchone()
    return row is not None


# ── Dashboard intelligence ─────────────────────────────────────────────────────

def get_top_opportunities(limit: int = 5) -> List:
    """Top TRACK events by demand_score."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT e.id, e.artist, e.city, e.country, e.event_date, e.venue,
                   e.face_value_min, e.tm_url, e.status, e.capacity,
                   a.demand_score, a.resale_potential, a.sellout_eta, a.reason,
                   s.secondary_low, s.spread_pct, s.net_profit_est
            FROM events e
            JOIN (
                SELECT event_id, demand_score, resale_potential,
                       sellout_eta, reason
                FROM analysis
                WHERE verdict = 'TRACK'
                ORDER BY ts DESC
            ) a ON a.event_id = e.id
            LEFT JOIN (
                SELECT event_id, secondary_low, spread_pct, net_profit_est
                FROM snapshots ORDER BY ts DESC
            ) s ON s.event_id = e.id
            ORDER BY a.demand_score DESC, s.net_profit_est DESC
            LIMIT ?
        """, (limit,)).fetchall()


def get_country_stats() -> List:
    """Event counts broken down by country."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT country,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status='ON_SALE' THEN 1 ELSE 0 END)  AS on_sale,
                   SUM(CASE WHEN status='SOLD_OUT' THEN 1 ELSE 0 END) AS sold_out
            FROM events
            GROUP BY country
            ORDER BY total DESC
        """).fetchall()


def get_total_profit_potential() -> float:
    """Sum of net_profit_est for all TRACK events with snapshots."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT SUM(s.net_profit_est) AS total
            FROM events e
            JOIN (
                SELECT event_id FROM analysis WHERE verdict = 'TRACK'
                ORDER BY ts DESC
            ) a ON a.event_id = e.id
            LEFT JOIN (
                SELECT event_id, net_profit_est
                FROM snapshots ORDER BY ts DESC
            ) s ON s.event_id = e.id
            WHERE s.net_profit_est IS NOT NULL
        """).fetchone()
    return float(row["total"] or 0)


# ── Users (auth) ───────────────────────────────────────────────────────────────

def create_user(username: str, password_hash: str, role: str = "user") -> int:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (username.strip().lower(), password_hash, role, now),
        )
        return cur.lastrowid


def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username.strip().lower(),),
        ).fetchone()


def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def list_users() -> List:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, username, role, created_at, last_login FROM users ORDER BY id"
        ).fetchall()


def update_user_password(user_id: int, password_hash: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )


def update_user_role(user_id: int, role: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET role = ? WHERE id = ?", (role, user_id)
        )


def update_last_login(user_id: int):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET last_login = ? WHERE id = ?", (now, user_id)
        )


def delete_user(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def count_admin_users() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM users WHERE role = 'admin'"
        ).fetchone()
    return int(row["n"] or 0)


# ── Bulk Account Update ────────────────────────────────────────────────────────

# Fields that are safe to bulk-update (never include 'id', 'created_at')
_BULK_EDITABLE_FIELDS = {
    "account_name", "email", "password", "first_name", "last_name",
    "phone", "address1", "address2", "city", "postcode", "country", "region",
    "proxy", "imap_email", "imap_password", "imap_server",
    "notes", "status", "group_tag", "health", "tags",
}


def bulk_update_accounts(account_ids: List[int], field_updates: dict) -> dict:
    """
    Update specific fields on multiple accounts in one transaction.

    field_updates: {field_name: {"value": str, "mode": "replace"|"clear"}}
      - mode "replace": set field to value (only if value is not empty)
      - mode "clear":   set field to empty string

    Returns:
      {"updated": count, "skipped_fields": [unknown fields], "ids": [updated ids]}

    Safety rules:
      - Only _BULK_EDITABLE_FIELDS may be changed
      - Unknown fields are silently skipped (listed in return value)
      - "replace" with empty string is treated as no-op (use "clear" to wipe)
      - updated_at is always refreshed for changed rows
    """
    if not account_ids or not field_updates:
        return {"updated": 0, "skipped_fields": [], "ids": []}

    now            = datetime.utcnow().isoformat()
    safe_updates   = {}
    skipped_fields = []

    for field, spec in field_updates.items():
        if field not in _BULK_EDITABLE_FIELDS:
            skipped_fields.append(field)
            continue
        mode  = spec.get("mode", "replace")
        value = spec.get("value", "")
        if mode == "clear":
            safe_updates[field] = ""
        elif mode == "replace":
            if value == "" or value is None:
                continue   # empty replace is a no-op; must use "clear" explicitly
            safe_updates[field] = value

    if not safe_updates:
        return {"updated": 0, "skipped_fields": skipped_fields, "ids": []}

    sets = ", ".join(f"{f} = ?" for f in safe_updates) + ", updated_at = ?"
    vals = list(safe_updates.values()) + [now]

    placeholders = ",".join("?" * len(account_ids))
    updated_ids  = []

    with get_conn() as conn:
        for acc_id in account_ids:
            conn.execute(
                f"UPDATE accounts SET {sets} WHERE id = ?",
                vals + [acc_id],
            )
            updated_ids.append(acc_id)

    return {
        "updated":        len(updated_ids),
        "skipped_fields": skipped_fields,
        "ids":            updated_ids,
        "fields_changed": list(safe_updates.keys()),
    }


def list_accounts_by_ids(account_ids: List[int]) -> List[dict]:
    """Fetch multiple accounts by ID list (for bulk-edit preview/sync)."""
    if not account_ids:
        return []
    placeholders = ",".join("?" * len(account_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM accounts WHERE id IN ({placeholders})",
            account_ids,
        ).fetchall()
    return [dict(r) for r in rows]
