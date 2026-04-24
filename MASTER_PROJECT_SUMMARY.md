# Entradas Ticket Scout v2 — Master Project Summary
> Last updated: 2026-04-19 (Session 9 — Documentation)
> Purpose: Permanent working handoff document. Do not summarise away business rules.
> For future sessions: read this document first, then code.
> Document location: `/Users/petartomsic/Desktop/ticketscout_v2/MASTER_PROJECT_SUMMARY.md`

---

## 1. Product Overview

**Entradas Ticket Scout v2** is a private, password-protected business intelligence and operations tool for a ticket resale / scalping operation.

The app has two distinct halves:
1. **Scout Engine** — discovers upcoming concerts/events via Ticketmaster API, scores them for resale potential using heuristics + Claude AI analysis, detects restocks, and sends Discord/Telegram alerts.
2. **Operations Dashboard** — manages the user's ticket portfolio (Google Sheets backed), accounts database (SQLite), CSV batch builder for automation tools, expenses tracking, and a recurring expenses calendar.

The UI is a single-page Flask web app served at `localhost:5001` (macOS AirPlay Receiver permanently owns port 5000). It runs locally on the owner's machine. No public hosting yet.

---

## 2. Main Goal of the App

Help the operator:
- **Find high-demand events** before general sale and before prices spike
- **Track secondary market prices** to calculate resale profit
- **Detect restocks** (new ticket batches appearing after sellout) automatically
- **Manage purchased tickets** (inventory, cost, revenue, P&L)
- **Manage buyer accounts** used for ticket purchasing automation
- **Build CSV files** for the automation tool (exact column spec, no deviations)
- **Track business expenses** with a recurring calendar
- **Receive alerts** via Discord and Telegram for high-priority events

---

## 3. Current Architecture / Data Sources

### Python / Flask backend
| File | Purpose | Lines |
|------|---------|-------|
| `app.py` | Flask app entry point, scheduler integration, all core routes | 940 |
| `main.py` | Standalone CLI scheduler (run separately from UI) | 390 |
| `config.py` | All env var parsing, constants | 76 |
| `database.py` | SQLite schema + all DB operations | 1099 |
| `auth.py` | Login, sessions, rate limiting, password hashing | 150 |
| `sheets_service.py` | All Google Sheets API logic (tickets + expenses) | 883 |
| `ticket_routes.py` | Flask Blueprint: ticket + expense API routes | 302 |
| `accounts_routes.py` | Flask Blueprint: accounts, batches, proxies, CSV | 788 |
| `recurring_expenses.py` | Recurring expense rules engine (new) | 461 |
| `analyzer.py` | Claude AI event analysis | 460 |
| `tm_client.py` | Ticketmaster Discovery API client | 279 |
| `secondary_market.py` | Secondary market price fetching | 313 |
| `restock_monitor.py` | Restock detection | 126 |
| `heuristic_scorer.py` | Fast scoring before Claude | 225 |
| `velocity_tracker.py` | Price velocity tracking | 154 |
| `venue_capacity.py` | Venue capacity lookup | 530 |
| `discord_alerts.py` | Discord webhook alerts + daily digest | 442 |
| `telegram_alerts.py` | Telegram Bot API alerts | 204 |
| `signup_runner.py` | Account signup automation | 512 |
| `signup_playwright.py` | Playwright-based signup automation | 491 |
| `ticketmanager_client.py` | TicketManager API client | 234 |

### Frontend
- `templates/index.html` — **8,014 lines / ~340 KB** — single SPA template, all CSS + HTML + JS inline
- `templates/login.html` — 357 lines — standalone dark-theme login page

### Project Size (confirmed Session 9)
- **Total project on disk**: ~4.8 MB
- **SQLite database**: ~4.1 MB (85% of total — all event/account data)
- **Python source files**: ~400 KB / ~8,800 lines across 21 files
- **HTML template**: ~340 KB / 8,014 lines
- **SQLite theoretical max**: 281 TB — no practical storage limit for this use case

### Databases
- **SQLite** (`ticketscout.db`, ~4.1 MB currently) — scout data, accounts, users, alert history, watchlist, CSV batches, proxies
  - WAL mode enabled
  - Safe migration via `_migrate_db()` in `database.py`
- **Google Sheets** — ticket inventory + historical expenses (read/write via service account)
  - Spreadsheet ID: `185iC33kamojK2qecHwtgjUUvg74G5lQ4am7tA9FHHmk`
  - Tabs: `Ticket Data`, `Expenses`, `Financial Summary`, `Recurring Expenses` (auto-created)

### Key External APIs
| Service | Purpose |
|---------|---------|
| Ticketmaster Discovery API | Event discovery, availability status |
| Claude AI (claude-opus-4-6) | Event demand analysis |
| Discord Webhook | TRACK alerts, RESTOCK alerts, daily digest |
| Telegram Bot API | Parallel alerts (currently `ENABLE_TELEGRAM=false`) |
| Google Sheets API | Ticket inventory + expense storage |

### Environment
- Default port: **5001** (macOS AirPlay occupies 5000 permanently)
- Start command: `python3 app.py --port 5001`
- `.env` file in project root — contains all credentials

---

## 4. Existing Modules (Pages/Sections in the UI)

### 4.1 Scout Dashboard (main page)
- Opportunities list with demand score, spread %, net profit estimate
- Filter toolbar: country, status, demand score, profit potential
- Watchlist star per event (⭐)
- "Add to Batch" button per event
- Top Opportunities widget, Profit Potential card, Country breakdown chart (Chart.js)
- Alert History accordion

### 4.2 Events List
- Full events table with capacity bar in venue column
- Per-row 🧠 Analyse button
- Watchlist star column

### 4.3 Ticket Management (Google Sheets backed)
Sub-tabs: Dashboard | All Tickets | Expenses | Summary

**Dashboard tab:** Invested, Revenue, Gross Profit, Net Profit, Qty cards + delivery/payout stats

**All Tickets tab:** Full ticket table with sort, filter (event/venue/delivery/payout/sold status), pagination, edit, duplicate, delete

**Expenses tab (extended with calendar):**
- Summary bar: Total Expenses, Number of Entries, Avg per Entry, This Month (Recurring)
- Sub-tabs: 📋 History | 📅 Calendar | ⏰ Upcoming | 🔄 Recurring Rules
- One-time expense modal (existing): date, amount, description, category, linked event, notes
- Recurring expense modal (new): name, amount, currency, category, vendor, day of month, start date, end date, payment method, color tag, notes, active toggle

**Summary tab:** P&L overview pulling live from Sheets

### 4.4 Accounts Database
- Full account list with search, filter (group, country, health, status), sort, pagination
- Account fields: name, email, password (masked), first/last name, phone, address, city, postcode, country, region, proxy, IMAP, notes, status, group tag, health, tags
- Account health states: fresh, used, flagged, banned
- Import from clipboard (colon-separated key:value format)
- Account duplication
- Signup history per account

### 4.5 CSV Batch Builder
- Batch config: target URL, mode (buy/monitor), proxy, ticket count, price range, resale, presale code, queue wait, sections, ACO profile, etc.
- Assign accounts to batch (drag-order preserved)
- Per-row overrides (any field can be overridden per account row)
- Live CSV preview before export
- Download CSV file
- Batch cloning
- Batch templates (save/load configs)
- Export history log

### 4.6 Proxies
- Full CRUD for proxy records
- Proxy test/status tracking

### 4.7 Settings
- API keys: Anthropic, Ticketmaster, Discord Webhook, Restock Webhook, Discord Role ID
- Telegram: Bot Token, Chat ID, Test button
- Notifications: Enable toggle, Digest Hour, Send Now button
- Scout Filters: min demand score, min resale potential, min spread %, venue capacity range
- Scheduler: Discovery interval, Snapshot interval, Restock interval, Analysis interval
- User Management: list users, add user, delete user, change password

### 4.8 Live Log
- SSE stream of backend log output displayed in real time in the UI

---

## 5. Completed Work So Far

### Session 1 — Foundation
- Flask app with sidebar navigation
- SQLite database with events, snapshots, analysis, restock_alerts tables
- Ticketmaster Discovery API client
- Basic event list display

### Session 2 — Scout Intelligence
- Secondary market price fetching
- Heuristic scorer (fast pre-filter before Claude)
- Velocity tracker
- Venue capacity lookup
- Claude AI analysis integration
- Discord alerts (TRACK, RESTOCK)

### Session 3 — Accounts Database + CSV Builder
- Accounts table in SQLite with full CRUD
- CSV Builder with exact column spec (`CSV_COLUMNS` / `CSV_MAPPING`)
- Batch system (create, configure, assign accounts, export)
- Per-row overrides
- Batch templates and export history
- Account import from clipboard (colon-separated format)
- Proxy management

### Session 4 — Dashboard Intelligence + Watchlist
- Top Opportunities widget
- Profit Potential card
- Country breakdown chart (Chart.js stacked bar)
- Watchlist system (star toggle per event)
- Opportunities filter toolbar
- "Add to Batch" button per event
- Alert History panel
- Telegram alerts module (`telegram_alerts.py`)
- Daily digest (Discord + Telegram)

### Session 5 — Notification Settings UI
- Notification settings section in Settings page
- Telegram configuration in Settings (Bot Token, Chat ID, Test button)
- Enable/disable notification toggle
- Digest Hour setting
- Send Now button

### Session 6 — Ticket Management (Google Sheets)
- `sheets_service.py` — full Google Sheets service layer
- `ticket_routes.py` — Flask Blueprint with CRUD for tickets and expenses
- Ticket Management page with 4 sub-tabs
- Financial calculations: gross profit, net profit, P&L
- Expense tracking (one-time, stored in Google Sheets "Expenses" tab)
- Financial blur toggle (privacy mode)

### Session 7 — Security / Login Wall
- `auth.py` — PBKDF2-SHA256 password hashing (werkzeug), IP rate limiting, session management
- Persistent `SECRET_KEY` generated once and saved to `.env` (survives restarts)
- Flask `before_request` whitelist approach — ALL routes protected by default
- 8-hour session lifetime
- Multi-user support in SQLite (`users` table)
- Default admin bootstrapped on first run (`admin` / `admin123` — MUST CHANGE)
- Login page (`templates/login.html`) — premium dark theme matching main app
- User Management in Settings: list users, add, delete, reset password
- Last-admin guard (cannot delete the only admin)
- Self-delete guard (cannot delete own account)
- Rate limiting: max 5 failed attempts per 60s per IP

### Session 8 — Recurring Expenses Calendar (most recent)
- `recurring_expenses.py` — 461-line recurring rule engine
  - One row per rule in Google Sheets "Recurring Expenses" tab (auto-created)
  - Calendar occurrences generated dynamically — no duplicate future rows
  - Day clamping: day 31 in February → Feb 28/29 (correct last-day-of-month)
  - Active/inactive/cancelled state management with `cancelledAt` date
- 7 new API routes in `ticket_routes.py`
- Expenses sub-tabs: History | Calendar | Upcoming | Recurring Rules
- Calendar grid view with month navigation, colored chips per category
- Upcoming expenses view (7/14/30/60/90 day selector)
- Recurring rules table with toggle (⏸/▶️), edit, delete
- Add/Edit Recurring Expense modal with all fields
- Recurring detail modal (click chip in calendar)
- Monthly total card in summary bar

---

## 6. Current Session Status

- **App is running**: `python3 app.py --port 5001` → `http://localhost:5001`
- **All syntax checks pass**: `python3 -m py_compile` on all .py files
- **HTML div balance**: 781 opens / 781 closes / diff=0
- **File sizes**: index.html = 8,014 lines, total project = ~8,800 Python lines
- **Port confirmed**: 5001 (macOS AirPlay permanently occupies 5000; confirmed this session — requires sudo to kill, not worth it)
- **MASTER_PROJECT_SUMMARY.md created**: This document, Session 9 — 677+ lines covering all sessions
- **Recurring expenses (Session 8)**: Fully built, syntax verified, div-balanced, running

---

## 7. Important Business Rules

### Resale Economics
- `FRICTION_RATE = 0.25` (25% default) — Viagogo platform fees + payment processing
- Net profit calculation: `gross_profit × (1 - friction_rate)`
- Minimum net profit to trigger TRACK alert: `MIN_NET_PROFIT_GBP = £40`
- Fast-lane heuristic score threshold: `FAST_LANE_SCORE = 70` → immediate Claude
- Batch score threshold: `HEURISTIC_BATCH_SCORE = 40` → hourly Claude batch
- Minimum resale spread: `MIN_SPREAD_PCT = 0.30` (30% above face value)
- Minimum demand score to alert: `MIN_DEMAND_SCORE = 7` (out of 10)

### Venue Capacity Filters
- `MAX_VENUE_CAPACITY = 20000` — filters out mega-festivals
- `MIN_VENUE_CAPACITY = 200` — allows small intimate venues (target market)

### Scout Regions
- Countries scanned: GB, DE, NL, FR, IT, ES, AT, CH (configurable)
- Categories: music (configurable)
- Horizon: 12 months ahead

### Scheduler Timing
- Restock check: every 5 min
- Full TM discovery: every 60 min
- Claude analysis: every 65 min (batch)
- Price snapshots: every 15 min

---

## 8. UI / Design Constraints

**CRITICAL — NON-NEGOTIABLE:**
- **Do NOT redesign the app**
- **Do NOT change visual identity, color scheme, typography, or layout**
- **Always reuse existing CSS variables**: `--bg`, `--surface`, `--surface2`, `--border`, `--border2`, `--text`, `--text-muted`, `--text-soft`, `--neon` (#00ff88), `--red` (#ff4d6a), `--orange`, `--blue`
- **Reuse existing component patterns**: `btn`, `btn primary`, `btn sm`, `btn sm danger`, `badge`, `modal-overlay`, `modal`, `modal-header`, `modal-body`, `modal-footer`, `modal-close`, `table-wrap`, `table-toolbar`, `empty-row`, `page`, `page-header`, `page-content`, `settings-section`
- **Reuse existing typography**: `Inter` font, uppercase labels, `--text-muted` subtext
- **Reuse form patterns**: `tm-form-grid`, `tm-form-group`, `tm-form-section-title`, `full` grid class
- **Reuse tab patterns**: `ttab`/`tt-*` for main tabs, `exp-stab`/`exp-tab-*` for expense sub-tabs
- **Loading states**: use `empty-row` pattern in tables, `cal-loading` div pattern
- **New features must feel native** — a new developer should not be able to tell which features were added later

---

## 9. Data Accuracy / Financial Accuracy Requirements

- **All financial values displayed in EUR (€)** using `fmtEur()` JS function
- **`fin-val` CSS class** must be applied to all financial values (enables privacy blur toggle)
- **European number formatting**: `€1.234,56` style (period as thousands separator, comma as decimal)
- **Ticket P&L**: `gross_profit = (qty_sold × sale_price_per_ticket) - total_cost`
- **Net profit** in Summary tab includes expense deduction
- **Google Sheets numbers**: `_safe_num()` in `sheets_service.py` handles EU decimal comma, EU thousands dot, currency symbols, percentages, Unicode minus signs — do NOT bypass this parser
- **Historical expense totals must include cancelled/inactive recurring expenses** — past records are permanent
- **div balance check** should be run after any large HTML edits: `python3 -c "import re; c=open('templates/index.html').read(); print(len(re.findall(r'<div\b',c)), len(re.findall(r'</div>',c)))"`

---

## 10. CSV Builder Rules

**CRITICAL — these columns are an exact spec for an external automation tool. Any deviation breaks the tool.**

### Column order (exact, do not reorder):
```
task_id, event_url, mode, use_proxy, number_of_tickets, max_tickets,
min_price, max_price, include_resale, delay_between_accounts,
first_name, last_name, email, password, postal_code, phone_number,
proxy, presale_code, wait_queue, imap_email, imap_password, imap_server,
sections, aco_profile, monitor_wait_time, message, otp_provider
```

### Boolean columns (MUST output `true`/`false`, NOT `0`/`1` or `yes`/`no`):
- `use_proxy`, `include_resale`, `wait_queue`

### Field sources:
- `task_id` → batch name (label for the run)
- `event_url` → batch target URL
- Personal fields (first_name, last_name, email, password, postal_code, phone_number, proxy, imap_*) → from account record
- All other fields → from batch config

### Per-row overrides:
- Any field can be overridden per account row via `row_overrides` JSON stored in `batch_accounts`
- Overrides take precedence over batch defaults

### CSV generation:
- `generate_csv_bytes()` in `accounts_routes.py` uses `csv.DictWriter` with exact `CSV_COLUMNS` fieldnames
- Empty fields output as empty string — never `None` or `null`
- Boolean coercion happens in `_normalise()` — single source of truth
- `CSV_COLUMNS` and `CSV_MAPPING` in `accounts_routes.py` are the single sources of truth — change only there

---

## 11. Expenses / Recurring Logic Rules

### Historical Expenses (Google Sheets "Expenses" tab)
- One-time expenses stored per row: `id, date, description, amount, category, eventName, notes`
- These are NEVER touched by the recurring system
- Historical records must always remain visible in the History sub-tab
- Cancelled recurring expenses do NOT remove historical records
- Past totals must include all past expense rows including from cancelled recurring sources

### Recurring Expenses (Google Sheets "Recurring Expenses" tab — auto-created)
- **One row per rule** — never duplicated rows for future months
- Calendar occurrences are **generated dynamically** from rules at request time
- `getCalendarOccurrences(year, month)` in `recurring_expenses.py` is the engine

### Active/Inactive/Cancelled State
| Status | isActive | cancelledAt | Calendar behavior |
|--------|----------|-------------|-------------------|
| active | true | empty | Appears every month until endDate |
| inactive (toggled off) | false | set to toggle date | Stops from next month onward |
| cancelled | false | set to cancel date | Stops from next month onward |

### Date Clamping Rule (IMPORTANT)
- `dayOfMonth = 31` in February → occurrence on **Feb 28** (or Feb 29 in leap year)
- `dayOfMonth = 31` in April → occurrence on **Apr 30**
- Logic: `min(dayOfMonth, calendar.monthrange(year, month)[1])`
- Implemented in `_clamp_day()` in `recurring_expenses.py`
- Occurrences with clamped days show `dayClamped: true` and a `⚠` indicator in the calendar

### `_rule_active_in_month()` Logic
A rule fires in a given month only if ALL of:
1. `isActive == true`
2. `startDate <= last_day_of_month` (not started yet → skip)
3. `endDate` is empty OR `endDate >= first_day_of_month` (already expired → skip)
4. `cancelledAt` is empty OR `cancelledAt >= first_day_of_month` (cancelled before month → skip)

### Recurring Expense Fields
`id, name, amount, currency, category, dayOfMonth, startDate, endDate, isActive, status, vendor, paymentMethod, colorTag, notes, cancelledAt, createdAt, updatedAt`

### API Endpoints
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/expenses/calendar?year=&month=` | GET | Occurrences for a month |
| `/api/expenses/upcoming?days=30` | GET | Next N days |
| `/api/expenses/recurring?status=` | GET | List all rules |
| `/api/expenses/recurring` | POST | Create rule |
| `/api/expenses/recurring/<id>` | PUT | Update rule |
| `/api/expenses/recurring/<id>/toggle` | PATCH | Enable/disable toggle |
| `/api/expenses/recurring/<id>` | DELETE | Delete rule |

### Important: Route Ordering
Flask routes are registered with literal paths before wildcard paths:
- `GET /api/expenses/recurring` → safe (literal beats `<expense_id>`)
- `GET /api/expenses/calendar` → safe
- `GET /api/expenses/upcoming` → safe
- No ambiguity because literal routes always win in Werkzeug routing

---

## 12. Ticket Management Rules

### Google Sheets Connection
- Service account: `ticketscout-sa@ticketscout-sheets-35735.iam.gserviceaccount.com`
- All Sheets logic lives exclusively in `sheets_service.py` — routes never call Sheets directly
- `TICKET_COLUMN_MAP` and `EXPENSES_COLUMN_MAP` — flexible header aliases so different spreadsheet column names all work
- `_safe_num()` handles all numeric parsing (EU format aware)
- `reset_client()` forces reconnect after credential changes

### Ticket Financial Fields
- `totalCost` — what was paid for tickets
- `totalRevenue` = `qty_sold × sale_price_per_ticket`
- `grossProfit` = `totalRevenue - totalCost`
- `netProfit` = `grossProfit - totalExpenses` (in Summary tab only)
- `costPerTicket` = `totalCost / qtyBought` (calculated)
- `qtyUnsold` = `qtyBought - qtySold`

### Delivery Status Values: `PENDING`, `DELIVERED`, `PARTIAL`
### Payout Status Values: `UNPAID`, `PENDING`, `PAID`

### Tab on page load
- On navigating to Expenses tab, `loadExpenses()` AND `loadExpensesMonthlyTotal()` are both called
- `showExpTab('history')` is the default sub-tab

---

## 13. Notifications / Monitoring Status

### Discord (active)
- Main webhook: configured in `.env`
- Restock webhook: separate URL (falls back to main if not set)
- Role ping: optional, for restock alerts
- Alert types: TRACK (new high-potential event), RESTOCK (availability restored)
- Daily digest: sent at `DAILY_DIGEST_HOUR` (default 9:00 AM)
- All sent alerts logged to `alert_history` SQLite table

### Telegram (installed but inactive)
- `ENABLE_TELEGRAM=false` in `.env`
- `telegram_alerts.py` is complete and functional
- Bot Token and Chat ID fields in Settings UI
- Test connection button in Settings UI
- To activate: set `ENABLE_TELEGRAM=true`, `TELEGRAM_BOT_TOKEN=...`, `TELEGRAM_CHAT_ID=...` in `.env`

### SSE Log Stream
- Live log stream at `/api/logs/stream` via Server-Sent Events
- Displayed in "Live Log" sidebar section in UI
- All Python log output forwarded to `_log_queue`

---

## 14. Security / Deployment Status

### Current Security Implementation
- **Authentication**: PBKDF2-SHA256 (werkzeug), salt_length=16, 8-hour signed-cookie sessions
- **`SECRET_KEY`**: Generated once on first run, persisted in `.env` — sessions survive restarts
- **Rate limiting**: 5 failed attempts per 60s per IP — in-memory `defaultdict(list)` with timestamp pruning
- **Route protection**: `before_request` whitelist (`PUBLIC_ENDPOINTS = {"login", "logout", "static"}`) — impossible to accidentally expose a route
- **Default admin**: `admin` / `admin123` — **MUST be changed immediately after setup**
- **Last-admin guard**: Cannot delete the only admin account
- **Session management**: `login_user()`, `logout_user()`, `current_user_id()`, `current_username()`

### Current Deployment
- **Local only** — running on owner's Mac at `localhost:5001`
- **VPS deployment not yet done**
- To expose on network: `python3 app.py --host 0.0.0.0 --port 5001`
- For production: add HTTPS (nginx reverse proxy), change SECRET_KEY, change admin password

### Sensitive Files
- `.env` — contains all API keys, Google private key, SECRET_KEY
- `google-service-key.json` — Google service account key (also in .env)
- **Never commit `.env` to git**

---

## 15. Known Bugs / Risks / Sensitive Areas

### Python 3.9 Compatibility
- **IMPORTANT**: Python 3.9 does NOT support `int | None` union type syntax
- Use `Optional[int]` from `typing` instead of `int | None` in type hints
- Or omit type hints on functions that had this issue (`current_user_id()` in `auth.py`)
- This caused a startup crash once — be careful when adding type hints

### Port 5000 Conflict (confirmed permanent)
- macOS AirPlay Receiver permanently owns port 5000
- **Always use `--port 5001`** to start the app
- Confirmed this session: even `kill -9` without sudo doesn't free it; new process re-acquires port immediately
- To permanently fix: System Preferences → General → AirDrop & Handoff → AirPlay Receiver → disable
- Kill command for lingering app processes: `pkill -f "app.py"` then wait 1s before restarting

### Google Sheets Caching
- `_client` and `_sheet` are module-level globals in `sheets_service.py`
- After credential changes in Settings, `reset_client()` must be called to force reconnect
- If Sheets connection fails, all ticket/expense operations fail gracefully with error messages

### Recurring Expenses Sheet Auto-Creation
- "Recurring Expenses" tab is auto-created in Google Sheets on first write
- If Google Sheets is not connected, recurring expense creation fails
- Tab name configurable via `GOOGLE_SHEETS_RECURRING_EXPENSES_TAB` env var

### Large HTML File
- `templates/index.html` is 8,014 lines — be careful with large edits
- Always run div balance check after structural changes
- The `esc()` function (HTML escape) exists in the file at line ~4721
- `fmtEur()` function exists at line ~6030
- `escHtml()` and `escAttr()` added in Session 8 for calendar (attr-safe escaping)

### Scheduler vs App
- `main.py` (scheduler) and `app.py` (web UI) are separate processes
- They share the same SQLite database — WAL mode handles concurrent access
- Running both simultaneously is fine and intended

---

## 16. Current Priorities

1. ✅ Login wall + user management (Session 7)
2. ✅ Recurring expenses calendar (Session 8)
3. ✅ Master Project Summary document created (Session 9)
4. 🔲 **Change default admin password** — urgent, do before sharing with anyone
5. 🔲 **VPS deployment** — most critical for real-world use
6. 🔲 Test recurring expenses end-to-end (add first rule, verify calendar + upcoming)
7. 🔲 Configure Telegram (optional, infrastructure ready)
8. 🔲 Ticket Management — P&L improvements, month-by-month breakdown

---

## 17. Next Recommended Tasks

### Immediate (before going live)
1. **Change default admin password** — go to Settings → User Management → Change My Password
2. **VPS deployment**: Set up a €5-10/month VPS (Hetzner/DigitalOcean), use `--host 0.0.0.0`, add nginx + SSL
3. **Test recurring expenses**: Add one rule, verify it appears in calendar and upcoming

### Short term
4. **Activate Telegram**: Set `ENABLE_TELEGRAM=true` and credentials in `.env`
5. **Add more users**: Settings → User Management → Add User (for team members)
6. **Tune scout filters**: Adjust `MIN_DEMAND_SCORE`, `MIN_SPREAD_PCT`, `FRICTION_RATE` in Settings based on actual results

### Medium term (future sessions)
7. **Session 9 — AI Data Quality**: Improve Claude analysis prompts, add confidence scoring
8. **Session 10 — Automation Scale**: Batch signup automation improvements
9. **Ticket Management P&L Improvements**: Month-by-month P&L breakdown, charts
10. **Mobile-responsive layout**: Currently desktop-only

---

## 18. Non-Negotiables

1. **DO NOT redesign the UI** — dark theme, neon green (#00ff88), current layout are permanent
2. **DO NOT change CSV column order or field mapping** — it breaks the external automation tool
3. **DO NOT delete or hide historical expense records** — cancelled recurring expenses must remain in History
4. **DO NOT generate future rows for recurring expenses** — always derive from rules dynamically
5. **DO NOT bypass `_safe_num()` for financial values from Google Sheets** — it handles EU number formats
6. **DO NOT use `int | None` type syntax** — Python 3.9 incompatible, use `Optional[int]` or no hint
7. **DO NOT touch the Google Sheets "Expenses" tab structure** — historical data lives there
8. **DO NOT skip the `fin-val` CSS class** on financial values — it powers the privacy blur
9. **DO NOT hardcode port 5000** — always use 5001 or `--port` argument
10. **Always check div balance** after large HTML edits

---

## 19. Short Daily Context Block

```
App: Entradas Ticket Scout v2 — local Flask app at localhost:5001
Summary doc: /Users/petartomsic/Desktop/ticketscout_v2/MASTER_PROJECT_SUMMARY.md
Start: cd /Users/petartomsic/Desktop/ticketscout_v2 && python3 app.py --port 5001
Kill old instance: pkill -f "app.py" && sleep 1
DB: SQLite (ticketscout.db ~4.1MB) + Google Sheets (tickets + expenses)
Auth: Login required, admin/admin123 (CHANGE THIS), 8h session, rate limited
UI: Single dark-theme SPA — DO NOT REDESIGN, reuse all existing components
Key files: app.py (940), database.py (1099), sheets_service.py (883), index.html (8014 lines)
Blueprints: ticket_routes.py (tickets+expenses), accounts_routes.py (accounts+CSV)
Recurring: recurring_expenses.py — rules in GSheets "Recurring Expenses" tab, occurrences generated dynamically
CSV: CSV_COLUMNS in accounts_routes.py = exact spec, boolean cols = true/false only
Financial: All values in EUR, use fmtEur(), apply fin-val class, never bypass _safe_num()
Python version: 3.9 — no `int | None` union syntax, use Optional[int]
Port: 5001 always (macOS AirPlay owns 5000 — cannot kill without disabling in System Preferences)
Div check: python3 -c "import re; c=open('templates/index.html').read(); print(len(re.findall(r'<div\b',c)), len(re.findall(r'</div>',c)))"
```

---

## 20. Session-by-Session Changelog

### Session 1 — Foundation
- Created Flask app, sidebar navigation, SQLite DB schema
- Tables: events, snapshots, analysis, restock_alerts
- Ticketmaster Discovery API client (`tm_client.py`)
- Basic event list display in UI

### Session 2 — Scout Intelligence
- `secondary_market.py` — secondary market price scraping
- `heuristic_scorer.py` — fast pre-filter (score 0–100)
- `velocity_tracker.py` — price velocity tracking
- `venue_capacity.py` — capacity lookup
- `analyzer.py` — Claude AI analysis integration
- `discord_alerts.py` — TRACK and RESTOCK Discord webhooks
- Fast-lane pipeline: score ≥ 70 → immediate Claude → immediate Discord
- Scheduler in `main.py` with counter-based intervals

### Session 3 — Accounts Database + CSV Builder
- `accounts_routes.py` Blueprint (788 lines)
- SQLite tables: accounts, proxies, batch_templates, export_history, signup_history, csv_batches, batch_accounts
- Full accounts CRUD with search/filter/sort/pagination
- CSV Builder: `CSV_COLUMNS` + `CSV_MAPPING` single source of truth
- Per-row overrides system
- Batch templates, export history
- Account import from clipboard (colon-separated format)
- Proxy management

### Session 4 — Dashboard Intelligence + Watchlist + Alerts
- Dashboard widgets: Top Opportunities, Profit Potential, Country Chart (Chart.js)
- Watchlist system (SQLite `watchlist` table, star toggle UI)
- Opportunities filter toolbar
- "Add to Batch" button on events
- Alert History panel
- `telegram_alerts.py` module (complete but inactive)
- `discord_alerts.py` extended: daily digest, alert DB logging
- SQLite tables added: alert_history, watchlist

### Session 5 — Notification Settings UI
- Telegram settings in Settings page (Bot Token, Chat ID, Test button)
- Notification enable/disable toggle
- Daily Digest Hour setting
- Send Daily Digest Now button
- Settings saving includes all new fields

### Session 6 — Ticket Management (Google Sheets)
- `sheets_service.py` (883 lines) — full Sheets service layer with EU number parsing
- `ticket_routes.py` Blueprint
- Ticket Management page: Dashboard, All Tickets, Expenses, Summary sub-tabs
- Ticket CRUD: add, edit, duplicate, delete, status updates
- Expense CRUD (one-time): stored in Google Sheets "Expenses" tab
- Financial calculations: gross profit, net profit, P&L
- `TICKET_COLUMN_MAP` + `EXPENSES_COLUMN_MAP` flexible header aliases
- Financial blur/hide toggle (privacy mode, `fin-val` class)

### Session 7 — Security / Login Wall
- `auth.py` (150 lines) — PBKDF2-SHA256, rate limiting, session management
- Persistent SECRET_KEY generation + storage in `.env`
- `before_request` whitelist protection (all routes locked by default)
- `templates/login.html` — dark-theme login page
- SQLite `users` table, `db.create_user()`, `db.get_user_by_username()`, etc.
- Default admin bootstrap: `admin` / `admin123`
- User Management UI in Settings: list, add, delete, reset password
- Last-admin guard, self-delete guard
- **Bug fixed**: Python 3.9 `int | None` type syntax crash in `auth.py`

### Session 8 — Recurring Expenses Calendar ✅
- `recurring_expenses.py` (461 lines) — recurring rule engine
  - Auto-creates "Recurring Expenses" GSheets tab on first use
  - Dynamic occurrence generation (no future rows in DB)
  - Day clamping for short months (`_clamp_day()`)
  - `_rule_active_in_month()` with startDate/endDate/cancelledAt logic
  - `getCalendarOccurrences()`, `getUpcomingExpenses()`, `getMonthlyTotal()`
- 7 new routes added to `ticket_routes.py` (ticket_routes.py grew to 302 lines)
- Expenses tab restructured into 4 sub-tabs: History | Calendar | Upcoming | Recurring Rules
- Calendar grid with month nav, colored category chips per category, detail modal on click
- Upcoming list with Today/This Week/This Month/Later grouping, days selector (7/14/30/60/90)
- Recurring rules table with toggle (⏸/▶️), edit, delete
- New "This Month (Recurring)" card added to Expenses summary bar
- New `expHtml()` and `escAttr()` JS helpers added (attr-safe escaping for calendar)
- All div balance checks pass (781/781)
- Syntax verified: `python3 -m py_compile recurring_expenses.py ticket_routes.py` → OK

### Session 9 — Documentation & Housekeeping ✅
- Answered file size questions: total project 4.8 MB, SQLite 4.1 MB (85%), index.html 8,014 lines
- Answered SQLite capacity: theoretical max 281 TB, no practical limit for this use case
- Resolved port conflict: macOS AirPlay permanently owns 5000; confirmed 5001 as the permanent port
- Port kill procedure confirmed: `pkill -f "app.py"` then `sleep 1` then restart on 5001
- Created `MASTER_PROJECT_SUMMARY.md` — 680+ lines, all 20 sections, full session changelog
- Created "Ultra Short Working Context" 20-bullet paste block for future sessions

---

---

# Ultra Short Working Context
*(Paste at the top of future sessions)*

```
ENTRADAS TICKET SCOUT v2 — Working Context (updated Session 9)
===============================================================
• Summary doc: /Users/petartomsic/Desktop/ticketscout_v2/MASTER_PROJECT_SUMMARY.md — read first
• Private Flask app: ticket scouting + operations. localhost:5001 (NOT 5000 — AirPlay owns it permanently)
• Start: pkill -f "app.py" 2>/dev/null; sleep 1; cd /Users/petartomsic/Desktop/ticketscout_v2 && python3 app.py --port 5001
• Storage: SQLite (ticketscout.db ~4.1MB) for scout/accounts/auth + Google Sheets for tickets+expenses
• Auth: PBKDF2 login, 8h sessions, rate limiting, before_request whitelist. Default admin/admin123 — CHANGE IT
• UI: Single dark-theme SPA, 8014-line index.html. DO NOT REDESIGN. Reuse all existing CSS vars and components.
• CSS vars: --neon (#00ff88), --red (#ff4d6a), --bg, --surface, --surface2, --border, --text, --text-muted
• Main modules: app.py, database.py, sheets_service.py, ticket_routes.py, accounts_routes.py, recurring_expenses.py
• Blueprints: ticket_bp (tickets+expenses CRUD), accounts_bp (accounts+CSV+proxies)
• Recurring expenses: rules in GSheets "Recurring Expenses" tab, occurrences generated dynamically. NEVER write future rows.
• Day clamp rule: day 31 in Feb → Feb 28/29. _clamp_day() in recurring_expenses.py.
• Historical expenses: NEVER delete or hide. Cancelled recurring still shows in History. Past totals include cancelled.
• CSV Builder: CSV_COLUMNS in accounts_routes.py = EXACT SPEC for external tool. Boolean cols = true/false only.
• Financial: All EUR, fmtEur(), fin-val class on all values (privacy blur). Never bypass _safe_num() for GSheets data.
• Python 3.9: NO int | None union syntax. Use Optional[int] from typing or omit hint.
• Div balance check: python3 -c "import re; c=open('templates/index.html').read(); print(len(re.findall(r'<div\b',c)), len(re.findall(r'</div>',c)))"
• Sessions done: 1(foundation) 2(scout AI) 3(accounts+CSV) 4(dashboard+watchlist) 5(notifications) 6(ticket mgmt) 7(login wall) 8(recurring calendar) 9(docs)
• Immediate next: 1) Change admin password 2) Test recurring expenses end-to-end 3) VPS deployment
• Non-negotiables: no UI redesign, no CSV column reorder, no deleting expense history, no future-row generation for recurring
```
