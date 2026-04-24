"""
telegram_alerts.py — Telegram Bot alerts for Entradas Ticket Scout.

Setup:
    1. Create a bot via @BotFather on Telegram → copy token
    2. Add bot to your channel/group (or DM it)
    3. Get the chat_id  (use https://api.telegram.org/bot<TOKEN>/getUpdates)
    4. Set in .env:
         TELEGRAM_BOT_TOKEN=123456789:ABCdef...
         TELEGRAM_CHAT_ID=-100123456789
         ENABLE_TELEGRAM=true
"""

import logging
import os
from datetime import datetime
from typing import List

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

COUNTRY_FLAGS = {
    "GB": "🇬🇧", "UK": "🇬🇧",
    "DE": "🇩🇪", "NL": "🇳🇱", "FR": "🇫🇷",
    "IT": "🇮🇹", "ES": "🇪🇸", "AT": "🇦🇹",
    "CH": "🇨🇭", "BE": "🇧🇪", "PL": "🇵🇱",
}


def _flag(country: str) -> str:
    return COUNTRY_FLAGS.get((country or "").upper(), "🌍")


def _get_token() -> str:
    load_dotenv(override=True)
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def _get_chat_id() -> str:
    load_dotenv(override=True)
    return os.getenv("TELEGRAM_CHAT_ID", "")


def _enabled() -> bool:
    load_dotenv(override=True)
    return os.getenv("ENABLE_TELEGRAM", "false").lower() == "true"


def _send(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured Telegram chat."""
    token   = _get_token()
    chat_id = _get_chat_id()
    if not token or not chat_id:
        logger.warning("Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id":                  chat_id,
            "text":                     text,
            "parse_mode":               parse_mode,
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Telegram returned {resp.status_code}: {resp.text[:300]}")
            return False
        return True
    except requests.RequestException as e:
        logger.error(f"Telegram send error: {e}")
        return False


# ── Alert type 1: TRACK event ──────────────────────────────────────────────────

def send_track_alert(r: dict) -> bool:
    """Send a single TRACK event alert."""
    if not _enabled():
        return False

    flag      = _flag(r.get("country", ""))
    artist    = r.get("artist", "Unknown")
    city      = r.get("city", "?")
    date      = r.get("event_date", "?")
    venue     = r.get("venue", "?")
    score     = r.get("demand_score", 7)
    potential = r.get("resale_potential", "MEDIUM")
    tm_url    = r.get("tm_url", "")

    secondary  = r.get("secondary") or {}
    low_ask    = secondary.get("low_ask")
    net_profit = secondary.get("net_profit_est", 0)
    spread_pct = secondary.get("spread_pct", 0)
    face_min   = r.get("face_value_min")
    reason     = r.get("reason", "")

    pot_emoji = {"HIGH": "🔥🔥🔥", "MEDIUM": "🔥🔥", "LOW": "🔥"}.get(potential, "🔥")

    lines = [
        f"🎯 <b>TRACK ALERT — {flag} {artist}</b>",
        f"📍 {city}  |  📅 {date}",
        f"🏟 {venue}",
        "",
        f"{pot_emoji} <b>{potential}</b>  |  Score: <b>{score}/10</b>",
    ]
    if face_min:
        lines.append(f"💶 Face Value: <b>£{face_min:.0f}</b>")
    if low_ask:
        lines.append(f"💰 Secondary Low: <b>£{low_ask:.0f}</b>  (+{spread_pct * 100:.0f}%)")
    if net_profit:
        lines.append(f"💵 Est. Net Profit: <b>£{net_profit:.0f}</b>")
    if reason:
        lines.append(f"\n<i>{reason[:200]}</i>")
    if tm_url:
        lines.append(f'\n🎟 <a href="{tm_url}">Buy on Ticketmaster</a>')

    return _send("\n".join(lines))


# ── Alert type 2: RESTOCK ──────────────────────────────────────────────────────

def send_restock_alert(alert: dict) -> bool:
    """Send a single RESTOCK alert."""
    if not _enabled():
        return False

    flag   = _flag(alert.get("country", ""))
    artist = alert.get("artist", "Unknown")
    city   = alert.get("city", "?")
    date   = alert.get("event_date", "?")
    venue  = alert.get("venue", "?")
    tm_url = alert.get("tm_url", "")
    fv_min = alert.get("face_value_min")

    lines = [
        f"⚡ <b>RESTOCK ALERT — {flag} {artist}</b>",
        f"📍 {city}  |  📅 {date}",
        f"🏟 {venue}",
        "",
        "🔄 Tickets back on sale!",
    ]
    if fv_min:
        lines.append(f"💶 Face Value: <b>£{fv_min:.0f}</b>")
    if tm_url:
        lines.append(f'\n🎟 <a href="{tm_url}">BUY NOW — Ticketmaster</a>')

    return _send("\n".join(lines))


# ── Batch dispatch ─────────────────────────────────────────────────────────────

def send_track_alerts(results: List[dict], scan_count: int = 0):
    """Send all TRACK alerts. Capped at 20 to avoid flooding."""
    if not _enabled() or not results:
        return
    for r in results[:20]:
        send_track_alert(r)
    logger.info(f"✅ Sent {min(len(results), 20)} TRACK alerts to Telegram.")


def send_restock_alerts(alerts: List[dict]):
    """Send RESTOCK alerts."""
    if not _enabled() or not alerts:
        return
    for alert in alerts:
        send_restock_alert(alert)
    logger.info(f"🔄 Sent {len(alerts)} restock alerts to Telegram.")


# ── Daily digest ───────────────────────────────────────────────────────────────

def send_daily_digest(stats: dict):
    """Send a daily summary to Telegram."""
    if not _enabled():
        return
    total    = stats.get("total_events", 0)
    on_sale  = stats.get("on_sale", 0)
    tracked  = stats.get("tracked", 0)
    restocks = stats.get("restocks_detected", 0)
    profit   = stats.get("profit_potential", 0)

    text = (
        f"📊 <b>Entradas Daily Digest</b>\n\n"
        f"📦 Events: <b>{total}</b> total, <b>{on_sale}</b> on sale\n"
        f"🎯 TRACK Opportunities: <b>{tracked}</b>\n"
        f"🔄 Restocks Today: <b>{restocks}</b>\n"
        f"💰 Profit Potential: <b>£{profit:.0f}</b>\n\n"
        f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>"
    )
    _send(text)
    logger.info("✅ Daily digest sent to Telegram.")


# ── Connection test ────────────────────────────────────────────────────────────

def test_connection() -> bool:
    """Send a test message to verify the bot is working."""
    return _send(
        "✅ <b>Entradas Ticket Scout — Telegram Connected!</b>\n\n"
        "You'll receive TRACK alerts and restock notifications here.\n\n"
        f"<i>Test sent at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>"
    )
