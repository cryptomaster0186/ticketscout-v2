"""
config.py — Sve postavke iz .env fajla.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Claude API ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str      = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str           = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")

# ── Ticketmaster Discovery API ────────────────────────────────────────────────
# Besplatno: https://developer.ticketmaster.com
TM_API_KEY: str             = os.getenv("TM_API_KEY", "")
TM_BASE_URL: str            = "https://app.ticketmaster.com/discovery/v2"
TM_COUNTRIES: list          = os.getenv("TM_COUNTRIES", "GB,DE,NL,FR,IT,ES,AT,CH,BE,PL").split(",")
TM_CATEGORIES: list         = os.getenv("TM_CATEGORIES", "music,sports,arts").split(",")
TM_MAX_RESULTS: int         = int(os.getenv("TM_MAX_RESULTS", "200"))
TM_MONTHS_AHEAD: int        = int(os.getenv("TM_MONTHS_AHEAD", "12"))

# ── Discord ───────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL: str         = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_RESTOCK_WEBHOOK_URL: str = os.getenv("DISCORD_RESTOCK_WEBHOOK_URL", DISCORD_WEBHOOK_URL)
DISCORD_ROLE_ID: str             = os.getenv("DISCORD_ROLE_ID", "")  # @role za restock pings

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str    = os.getenv("TELEGRAM_CHAT_ID", "")
ENABLE_TELEGRAM: bool    = os.getenv("ENABLE_TELEGRAM", "false").lower() == "true"
DAILY_DIGEST_HOUR: int   = int(os.getenv("DAILY_DIGEST_HOUR", "9"))

# ── Scout filters ─────────────────────────────────────────────────────────────
MIN_DEMAND_SCORE: int       = int(os.getenv("MIN_DEMAND_SCORE", "7"))
MIN_RESALE_POTENTIAL: str   = os.getenv("MIN_RESALE_POTENTIAL", "MEDIUM")
MIN_SPREAD_PCT: float       = float(os.getenv("MIN_SPREAD_PCT", "0.30"))   # min 30% resale spread
MAX_VENUE_CAPACITY: int     = int(os.getenv("MAX_VENUE_CAPACITY", "20000"))
MIN_VENUE_CAPACITY: int     = int(os.getenv("MIN_VENUE_CAPACITY", "200"))   # lowered — small venues are the target

# ── Scheduler intervals (seconds) ─────────────────────────────────────────────
INTERVAL_DISCOVERY: int     = int(os.getenv("INTERVAL_DISCOVERY", "3600"))     # 1h — TM event discovery
INTERVAL_SNAPSHOT: int      = int(os.getenv("INTERVAL_SNAPSHOT", "900"))       # 15min — price snapshots
INTERVAL_RESTOCK: int       = int(os.getenv("INTERVAL_RESTOCK", "300"))        # 5min — restock check
INTERVAL_ANALYSIS: int      = int(os.getenv("INTERVAL_ANALYSIS", "3600"))      # 1h — Claude analysis

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH: str                = os.getenv("DB_PATH", "ticketscout.db")

# ── Google Sheets (Ticket Management) ────────────────────────────────────────
GOOGLE_PROJECT_ID: str      = os.getenv("GOOGLE_PROJECT_ID", "")
GOOGLE_PRIVATE_KEY_ID: str  = os.getenv("GOOGLE_PRIVATE_KEY_ID", "")
GOOGLE_CLIENT_EMAIL: str    = os.getenv("GOOGLE_CLIENT_EMAIL", "")
GOOGLE_PRIVATE_KEY: str     = os.getenv("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n")
GOOGLE_SHEETS_SPREADSHEET_ID: str = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")
GOOGLE_SHEETS_TICKETS_TAB: str    = os.getenv("GOOGLE_SHEETS_TICKETS_TAB", "Ticket Data")
GOOGLE_SHEETS_EXPENSES_TAB: str   = os.getenv("GOOGLE_SHEETS_EXPENSES_TAB", "Expenses")
GOOGLE_SHEETS_SUMMARY_TAB: str    = os.getenv("GOOGLE_SHEETS_SUMMARY_TAB", "Financial Summary")

# ── Secondary market ─────────────────────────────────────────────────────────
FRICTION_RATE: float        = float(os.getenv("FRICTION_RATE", "0.25"))  # 25% viagogo fees

# ── Profit & fast-lane thresholds ────────────────────────────────────────────
MIN_NET_PROFIT_GBP: float   = float(os.getenv("MIN_NET_PROFIT_GBP", "40"))  # min £40 net profit to TRACK
FAST_LANE_SCORE: int        = int(os.getenv("FAST_LANE_SCORE", "70"))        # heuristic score → immediate Claude
HEURISTIC_BATCH_SCORE: int  = int(os.getenv("HEURISTIC_BATCH_SCORE", "40")) # score → hourly batch

def validate():
    errors = []
    if not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY nije postavljen")
    if not TM_API_KEY:
        errors.append("TM_API_KEY nije postavljen (https://developer.ticketmaster.com)")
    if not DISCORD_WEBHOOK_URL:
        errors.append("DISCORD_WEBHOOK_URL nije postavljen")
    if errors:
        raise EnvironmentError("Config errors:\n" + "\n".join(f"  • {e}" for e in errors))
