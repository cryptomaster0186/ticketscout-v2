# TicketScout v2 — Project Context for Claude Code

## What This Is
TicketScout v2 is an automated EU/UK ticket resale intelligence system.
It finds profitable Ticketmaster events, monitors secondary market prices (Viagogo/StubHub),
detects restocks, and sends alerts to Discord.

## Core Idea
- **Input**: Events discovered automatically via Ticketmaster Discovery API
- **Analysis**: Claude API acts as a "reselling scout" — scores each event 1-10, flags TRACK/IGNORE
- **Signal**: Secondary market prices (Viagogo/StubHub) scraped and compared to face value
- **Output**: Discord alerts for profitable events + restock notifications
- **Database**: SQLite stores event history, price snapshots, velocity trends

## Architecture — File by File

### `config.py`
All settings loaded from `.env` file. Key variables:
- `ANTHROPIC_API_KEY` — Claude API
- `TM_API_KEY` — Ticketmaster Discovery API (free at developer.ticketmaster.com)
- `DISCORD_WEBHOOK_URL` — Main Discord channel for TRACK alerts
- `DISCORD_RESTOCK_WEBHOOK_URL` — Separate channel for restock alerts
- `MIN_DEMAND_SCORE` — Minimum score to flag (default: 7)
- `MIN_SPREAD_PCT` — Minimum resale spread (default: 0.30 = 30%)
- `FRICTION_RATE` — Viagogo fee estimate (default: 0.25 = 25%)
- `TM_COUNTRIES` — Countries to scan e.g. "GB,DE,NL,FR,IT,ES,AT,CH"

### `database.py`
SQLite layer with 4 tables:
- `events` — Master list of all discovered events (tm_id, artist, city, date, venue, capacity, face_value, status)
- `snapshots` — Periodic price checks (secondary_low, secondary_median, spread_pct, net_profit_est)
- `analysis` — Claude analysis results per event (verdict, demand_score, resale_potential, reason)
- `restock_alerts` — Detected restocks (prev_status, new_status, discord_sent flag)

Key functions: `upsert_event()`, `insert_snapshot()`, `insert_analysis()`, `insert_restock_alert()`

### `tm_client.py`
Ticketmaster Discovery API v2 client.
- `discover_all()` — Scans all configured countries/categories, returns list of event dicts
- `check_event_availability(tm_id)` — Checks current status of one event (for restock monitoring)
- Handles pagination, rate limiting, capacity filtering
- API base: `https://app.ticketmaster.com/discovery/v2/events.json`

### `secondary_market.py`
Secondary market price fetcher. Priority order:
1. **Viagogo** — scrapes search results page with browser-like headers
2. **StubHub** — fallback scraper
3. **Estimated** — mathematical estimate based on scarcity signals when scraping fails
Returns `SecondaryData` dataclass with: source, low_ask, median_ask, spread_pct, net_profit_est, confidence

### `velocity_tracker.py`
Analyses price trend from DB snapshots history.
- `analyse_velocity(event_id)` — Returns `VelocityReport` with trend (ACCELERATING/STABLE/COOLING)
- `format_for_claude(report)` — Formats for Claude prompt context
- Detects: price change %, spread trend (RISING/FLAT/FALLING), sellout signals

### `restock_monitor.py`
Monitors SOLD_OUT events for restocks.
- `run_restock_check()` — Checks all SOLD_OUT events, inserts restock_alert if status changes
- `run_status_update()` — Full status update for all events (run hourly)

### `analyzer.py`
Enhanced Claude API integration.
- `analyse_batch(event_ids)` — Sends batch of events to Claude with full context:
  - Face value + secondary market prices + spread calculation
  - Velocity trend from DB history
  - Comparable events from DB (same venue/country)
  - TM status
- Returns list of TRACK events with demand_score, resale_potential, reason, sellout_eta
- System prompt: Expert reselling scout, minimum score 7, TRACK requires spread ≥ 30%

### `discord_alerts.py`
Discord webhook sender with 3 alert types:
1. **TRACK alerts** — Rich embeds with spread %, net profit, demand bar, sellout ETA
2. **RESTOCK alerts** — Urgent blue embeds with @role ping
3. **Status update** — Periodic DB summary
- Color coding: HIGH=red, MEDIUM=orange, LOW=yellow, RESTOCK=blue

### `main.py`
CLI orchestrator with scheduler.
Commands: `run`, `discover`, `snapshot`, `restock`, `analyse`, `status`
Scheduler intervals (counter-based, no APScheduler):
- Every 5 min: restock check
- Every 15 min: price snapshots
- Every 60 min: TM discovery + Claude analysis

### `app.py`
Flask web dashboard (UI).
- 18 API routes covering all system functions
- Background thread scheduler
- SSE endpoint `/api/logs/stream` for live logs
- Config read/write to `.env` file

### `templates/index.html`
Single-page web UI (~46k chars).
Tabs: Dashboard | Events | Alerts | Controls | Live Logs | Settings
- Dashboard: 5 stat cards + recent TRACK + restock tables, auto-refresh 30s
- Events: Searchable/filterable table, click → detail modal with Chart.js price history
- Alerts: TRACK alerts table + restock alerts table
- Controls: Manual task triggers + scheduler on/off + Discord test
- Live Logs: SSE stream with color-coded entries (info/warning/error)
- Settings: Full config editor, saves to .env

## Scheduler Flow (24/7 mode)
```
startup → discover_all() → populate DB
every  5 min → restock_check() → Discord restock alert if found
every 15 min → snapshot() → fetch secondary prices → store in DB
every 60 min → discover_all() → add new events
every 65 min → analyse_batch() → Claude analysis → Discord TRACK alerts
every 12h    → status_update() → Discord summary
```

## Data Flow
```
TM API → events table
             ↓
secondary_market scraper → snapshots table
             ↓
velocity_tracker → trend analysis
             ↓
analyzer (Claude) → analysis table → Discord TRACK alert
             ↓
restock_monitor → restock_alerts table → Discord RESTOCK alert
```

## Key Design Decisions
- **No APScheduler dependency** — simple counter-based loop in main.py
- **Graceful degradation** — secondary market scraping always falls back to estimate
- **SQLite** — single file DB, no server needed, WAL mode for concurrency
- **Flask threaded=True** — handles concurrent requests while scheduler runs
- **Velocity from snapshots** — tracks price changes between checks, not just one-time read

## Pending Integration (NOT YET DONE)
- **TicketManager API** — user has API key for live sales data (sold_24h, remaining tickets)
  - This would replace estimated velocity with real sales velocity
  - Should be integrated as a new `ticketmanager_client.py` module
  - Key data needed: event_id mapping, sold_in_24h, remaining, on_sale_date

## Running
```bash
# Install
pip install -r requirements.txt
cp .env.example .env
# Fill .env with API keys

# Web UI
python app.py                    # http://localhost:5000
python app.py --host 0.0.0.0    # accessible on network

# CLI
python main.py run               # 24/7 scheduler
python main.py discover          # one-time discovery
python main.py --dry-run         # no Discord sending
```

## .env Required Keys
```
ANTHROPIC_API_KEY=sk-ant-...
TM_API_KEY=...                   # developer.ticketmaster.com (free)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

## Current Status
- All code complete and syntax-verified
- DB schema initialised and tested
- Flask UI running with 18 API endpoints
- Needs real API keys to run live
- TicketManager integration pending (see above)
