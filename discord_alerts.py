"""
discord_alerts.py — Premium Discord embeds for Entradas Ticket Scout.

Alert types:
  1. TRACK alert    — profitable event flagged (neon green / orange / red)
  2. RESTOCK alert  — sold-out event came back (urgent blue, @role ping)
  3. Status update  — periodic scan summary
"""

import logging
import os
from datetime import datetime
from typing import List

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ── Colours (Discord integer format) ──────────────────────────────────────────
COLOR_NEON    = 0x00FF88   # neon green  — HIGH potential / header
COLOR_ORANGE  = 0xFF9F2B   # orange      — MEDIUM potential
COLOR_GOLD    = 0xFFD700   # gold        — LOW potential
COLOR_RED     = 0xFF4D6A   # red accent  — sold out / urgent
COLOR_RESTOCK = 0x00BFFF   # blue        — restock (urgent)
COLOR_SUMMARY = 0x2D3748   # dark slate  — status summaries

COLOR_MAP = {
    "HIGH":   COLOR_NEON,
    "MEDIUM": COLOR_ORANGE,
    "LOW":    COLOR_GOLD,
}

POTENTIAL_LABEL = {
    "HIGH":   "🔥🔥🔥  HIGH",
    "MEDIUM": "🔥🔥  MEDIUM",
    "LOW":    "🔥  LOW",
}

COUNTRY_FLAGS = {
    "GB": "🇬🇧", "UK": "🇬🇧",
    "DE": "🇩🇪", "NL": "🇳🇱", "FR": "🇫🇷",
    "IT": "🇮🇹", "ES": "🇪🇸", "AT": "🇦🇹",
    "CH": "🇨🇭", "BE": "🇧🇪", "PL": "🇵🇱",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _flag(country: str) -> str:
    return COUNTRY_FLAGS.get((country or "").upper(), "🌍")


def _demand_bar(score: int) -> str:
    """5-block demand bar: 🟩🟩🟩⬜⬜"""
    score = max(0, min(10, int(score or 0)))
    filled = round(score / 10 * 5)
    return "🟩" * filled + "⬜" * (5 - filled) + f"  **{score}/10**"


def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


def _get_main_webhook() -> str:
    """Always reads fresh from env — works after hot-reload."""
    load_dotenv(override=True)
    return os.getenv("DISCORD_WEBHOOK_URL", "")


def _get_restock_webhook() -> str:
    load_dotenv(override=True)
    return os.getenv("DISCORD_RESTOCK_WEBHOOK_URL", "") or _get_main_webhook()


def _get_role_id() -> str:
    load_dotenv(override=True)
    return os.getenv("DISCORD_ROLE_ID", "")


def _get_friction() -> float:
    load_dotenv(override=True)
    try:
        return float(os.getenv("FRICTION_RATE", "0.25"))
    except (ValueError, TypeError):
        return 0.25


def _log_alert_to_db(alert_type: str, results=None, alert=None,
                     platform: str = "discord"):
    """Non-blocking DB log for alert history. Import lazily to avoid circular import."""
    try:
        import database as _db
        if alert_type == "TRACK" and results:
            for r in results:
                _db.log_alert(
                    alert_type="TRACK",
                    event_id=r.get("id"),
                    artist=r.get("artist", ""),
                    city=r.get("city", ""),
                    country=r.get("country", ""),
                    event_date=r.get("event_date", ""),
                    demand_score=r.get("demand_score"),
                    net_profit=(r.get("secondary") or {}).get("net_profit_est"),
                    platform=platform,
                )
        elif alert_type == "RESTOCK" and alert:
            _db.log_alert(
                alert_type="RESTOCK",
                event_id=alert.get("id"),
                artist=alert.get("artist", ""),
                city=alert.get("city", ""),
                country=alert.get("country", ""),
                event_date=alert.get("event_date", ""),
                platform=platform,
            )
    except Exception as e:
        logger.debug(f"Alert history log failed (non-critical): {e}")


def _send(webhook_url: str, payload: dict) -> bool:
    """POST to Discord webhook. Logs full error body on failure."""
    if not webhook_url or "XXXXXXXXXX" in webhook_url:
        logger.warning("Discord webhook not configured — skipping send.")
        return False
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code not in (200, 204):
            logger.error(
                f"Discord returned {resp.status_code}: {resp.text[:500]}"
            )
            return False
        return True
    except requests.RequestException as e:
        logger.error(f"Discord send error: {e}")
        return False


# ── Alert type 1: TRACK event ──────────────────────────────────────────────────

def _build_track_embed(r: dict) -> dict:
    """Builds one premium Discord embed for a TRACK event."""
    potential = r.get("resale_potential", "MEDIUM")
    score     = r.get("demand_score", 7)
    secondary = r.get("secondary") or {}
    friction  = _get_friction()

    flag    = _flag(r.get("country", ""))
    artist  = r.get("artist") or "Unknown Artist"
    city    = r.get("city") or "?"
    date    = r.get("event_date") or "?"
    venue   = r.get("venue") or "?"
    tm_url  = r.get("tm_url") or ""

    face_min = r.get("face_value_min")
    face_max = r.get("face_value_max")
    currency = r.get("currency", "GBP")

    low_ask    = secondary.get("low_ask")
    spread_pct = secondary.get("spread_pct", 0)
    net_profit = secondary.get("net_profit_est", 0)
    source     = secondary.get("source", "market").upper()
    eta        = r.get("sellout_eta") or ""
    reason     = r.get("reason") or ""

    # ── Description ──
    lines = []

    # Event info line
    lines.append(f"**{flag} {artist}  ·  {city}**")
    lines.append(f"📅  {date}   |   🏟  {venue}")
    lines.append("")

    # Price block
    if face_min:
        fv = f"{currency} {face_min:.0f}"
        if face_max and face_max != face_min:
            fv += f" – {face_max:.0f}"
        lines.append(f"**💶 Face Value**   `{fv}`")

    if low_ask:
        lines.append(f"**💰 Viagogo Low Ask**   `£{low_ask:.0f}`  *(source: {source})*")
        lines.append(
            f"**📈 Spread**   `+{spread_pct * 100:.0f}%`   "
            f"**💵 Est. Net**   `£{net_profit:.0f}`  *(after {friction*100:.0f}% fees)*"
        )

    if eta and eta.lower() not in ("unknown", ""):
        lines.append(f"**⏱ Sellout ETA**   {eta}")

    if reason:
        lines.append("")
        lines.append(f"*{reason[:300]}*")

    if tm_url:
        lines.append(f"\n[🎟  Buy on Ticketmaster]({tm_url})")

    description = "\n".join(lines)
    # Discord embed description max = 4096 chars
    if len(description) > 4000:
        description = description[:3997] + "..."

    # ── Fields ──
    fields = [
        {
            "name": "Demand Score",
            "value": _demand_bar(score),
            "inline": False,
        },
        {
            "name": "Resale Potential",
            "value": POTENTIAL_LABEL.get(potential, f"🔥 {potential}"),
            "inline": True,
        },
        {
            "name": "Status",
            "value": "✅  TRACK",
            "inline": True,
        },
    ]

    if r.get("action"):
        fields.append({
            "name": "Action",
            "value": f"`{r['action']}`",
            "inline": True,
        })

    return {
        "title":       f"🎯  {artist} — {city}  {flag}",
        "description": description,
        "color":       COLOR_MAP.get(potential, COLOR_ORANGE),
        "fields":      fields,
        "footer":      {
            "text": f"Entradas Ticket Scout  •  {_ts()}"
        },
    }


def send_track_alerts(results: List[dict], scan_count: int = 0):
    """
    Sends all TRACK events to Discord.
    Starts with a header embed, then batches of up to 10 event embeds.
    """
    webhook = _get_main_webhook()
    if not webhook:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping TRACK alerts.")
        return

    if not results:
        send_no_results(scan_count)
        return

    # ── Header message ──
    header_embed = {
        "title": "🎯  Entradas Ticket Scout — New Opportunities",
        "description": (
            f"**{len(results)}** profitable event{'s' if len(results) != 1 else ''} found "
            f"out of {scan_count} scanned.\n"
            f"*Filter: spread ≥ {int(float(os.getenv('MIN_SPREAD_PCT','0.3'))*100)}% · "
            f"score ≥ {os.getenv('MIN_DEMAND_SCORE','7')}*"
        ),
        "color": COLOR_NEON,
        "footer": {"text": f"Entradas Ticket Scout  •  {_ts()}"},
    }
    _send(webhook, {"username": "Entradas Ticket Scout", "embeds": [header_embed]})

    # Sort HIGH → MEDIUM → LOW, then by score descending
    sorted_r = sorted(
        results,
        key=lambda x: (
            ["LOW", "MEDIUM", "HIGH"].index(x.get("resale_potential", "LOW")),
            x.get("demand_score", 0),
        ),
        reverse=True,
    )

    # Send in batches of 10 (Discord limit per message)
    for i in range(0, len(sorted_r), 10):
        batch  = sorted_r[i : i + 10]
        embeds = [_build_track_embed(r) for r in batch]
        _send(webhook, {"username": "Entradas Ticket Scout", "embeds": embeds})

    logger.info(f"✅ Sent {len(results)} TRACK alerts to Discord.")
    _log_alert_to_db("TRACK", results=sorted_r)


# ── Alert type 2: RESTOCK ──────────────────────────────────────────────────────

def send_restock_alerts(alerts: List):
    """Sends urgent restock alerts — blue embed, optional @role ping."""
    if not alerts:
        return

    webhook = _get_restock_webhook()
    if not webhook:
        logger.warning("Discord restock webhook not set — skipping.")
        return

    role_id = _get_role_id()

    for alert in alerts:
        artist  = alert.get("artist", "Unknown")
        city    = alert.get("city", "?")
        country = alert.get("country", "")
        date    = alert.get("event_date", "?")
        venue   = alert.get("venue", "?")
        tm_url  = alert.get("tm_url", "")
        prev    = alert.get("prev_status", "SOLD_OUT")
        new     = alert.get("new_status", "ON_SALE")
        fv_min  = alert.get("face_value_min")
        fv_max  = alert.get("face_value_max")
        flag    = _flag(country)

        lines = [
            f"**{flag} {artist}  ·  {city}**",
            f"📅  {date}   |   🏟  {venue}",
            "",
            f"⚡  **Status changed:**  `{prev}` → `{new}`",
        ]

        if fv_min:
            fv = f"£{fv_min:.0f}" + (f" – £{fv_max:.0f}" if fv_max and fv_max != fv_min else "")
            lines.append(f"💶  **Face Value:**  {fv}")

        if tm_url:
            lines.append(f"\n[🎟  BUY NOW — Ticketmaster]({tm_url})")

        embed = {
            "title":       f"🔄  RESTOCK ALERT: {artist} — {city}",
            "description": "\n".join(lines),
            "color":       COLOR_RESTOCK,
            "footer":      {"text": f"Entradas Restock Monitor  •  {_ts()}"},
        }

        payload: dict = {
            "username": "Entradas Ticket Scout",
            "embeds": [embed],
        }
        if role_id:
            payload["content"] = f"<@&{role_id}> 🚨 **RESTOCK DETECTED — Act fast!**"

        _send(webhook, payload)

    logger.info(f"🔄 Sent {len(alerts)} restock alerts to Discord.")
    for a in alerts:
        _log_alert_to_db("RESTOCK", alert=a)


# ── Alert type 3: Daily digest ────────────────────────────────────────────────

def send_daily_digest(stats: dict):
    """Send a rich daily summary embed."""
    webhook = _get_main_webhook()
    if not webhook:
        return

    total    = stats.get("total_events", 0)
    on_sale  = stats.get("on_sale", 0)
    sold_out = stats.get("sold_out", 0)
    tracked  = stats.get("tracked", 0)
    restocks = stats.get("restocks_detected", 0)
    profit   = stats.get("profit_potential", 0)

    fields = [
        {"name": "📦 Total Events",    "value": f"`{total}`",          "inline": True},
        {"name": "🟢 On Sale",          "value": f"`{on_sale}`",        "inline": True},
        {"name": "🔴 Sold Out",         "value": f"`{sold_out}`",       "inline": True},
        {"name": "🎯 TRACK Flagged",    "value": f"`{tracked}`",        "inline": True},
        {"name": "🔄 Restocks Today",   "value": f"`{restocks}`",       "inline": True},
        {"name": "💰 Profit Potential", "value": f"`£{profit:.0f}`",    "inline": True},
    ]

    payload = {
        "username": "Entradas Ticket Scout",
        "embeds": [{
            "title":       "📅  Daily Digest — Entradas Ticket Scout",
            "description": "Your daily scouting summary.",
            "color":       COLOR_NEON,
            "fields":      fields,
            "footer":      {"text": f"Entradas Ticket Scout  •  {_ts()}"},
        }],
    }
    _send(webhook, payload)
    _log_alert_to_db("DIGEST")
    logger.info("✅ Daily digest sent to Discord.")


# ── Alert type 4: No results ───────────────────────────────────────────────────

def send_no_results(scan_count: int = 0):
    webhook = _get_main_webhook()
    if not webhook:
        return
    payload = {
        "username": "Entradas Ticket Scout",
        "embeds": [{
            "title":       "😴  No New Opportunities",
            "description": (
                f"Scanned **{scan_count}** events — none passed the current filters.\n"
                "*Filters may be too strict, or the market is quiet right now.*"
            ),
            "color":       COLOR_SUMMARY,
            "footer":      {"text": f"Entradas Ticket Scout  •  {_ts()}"},
        }]
    }
    _send(webhook, payload)


# ── Alert type 4: System status ────────────────────────────────────────────────

def send_status_update(stats: dict):
    """Periodic system summary sent to main webhook."""
    webhook = _get_main_webhook()
    if not webhook:
        return

    total    = stats.get("total_events", 0)
    on_sale  = stats.get("on_sale", 0)
    sold_out = stats.get("sold_out", 0)
    tracked  = stats.get("tracked", 0)
    restocks = stats.get("restocks_detected", 0)

    fields = [
        {"name": "Total Events",    "value": f"`{total}`",    "inline": True},
        {"name": "On Sale",         "value": f"`{on_sale}`",  "inline": True},
        {"name": "Sold Out",        "value": f"`{sold_out}`", "inline": True},
        {"name": "TRACK Flagged",   "value": f"`{tracked}`",  "inline": True},
        {"name": "Restocks Found",  "value": f"`{restocks}`", "inline": True},
    ]

    payload = {
        "username": "Entradas Ticket Scout",
        "embeds": [{
            "title":       "📊  System Status Update",
            "description": "Current snapshot of the Entradas Ticket Scout database.",
            "color":       COLOR_SUMMARY,
            "fields":      fields,
            "footer":      {"text": f"Entradas Ticket Scout  •  {_ts()}"},
        }]
    }
    _send(webhook, payload)
