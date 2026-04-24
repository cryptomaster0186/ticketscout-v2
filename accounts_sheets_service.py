"""
accounts_sheets_service.py — Google Sheets sync layer for the Accounts Base.

Follows the SAME pattern as sheets_service.py:
  - same service-account env-var auth
  - same _get_client() / _get_spreadsheet() caching
  - same _build_header_map() flexible header matching
  - same _safe_num() / _safe_date() parsers
  - same batch-update write strategy (ws.batch_update / ws.update)
  - same canonical headers → auto-create tab if missing

Spreadsheet : GOOGLE_SHEETS_SPREADSHEET_ID  (same workbook as tickets/expenses)
Tab name     : GOOGLE_SHEETS_ACCOUNTS_TAB   (default: "Accounts Base")

SYNC MODEL
----------
SQLite is the operational source of truth (required for batch FK relationships).
Google Sheets is the human-readable mirror / manual-edit surface.

  UI write → SQLite (fast, relational)
           → background thread → Sheets (human-readable, manual-editable)

  Sheets manual edit → "Import from Sheets" → updates SQLite records

All Sheets writes are best-effort: a Sheets failure never blocks the API response.
"""

import logging
import os
import threading
from datetime import datetime
from typing import Any, Optional, List

logger = logging.getLogger(__name__)

# ── Canonical headers (written when the tab is auto-created) ──────────────────
ACCOUNTS_CANONICAL_HEADERS: List[str] = [
    "ID",
    "Account Name",
    "Email",
    "Password",
    "First Name",
    "Last Name",
    "Phone",
    "Address 1",
    "Address 2",
    "City",
    "Postcode",
    "Country",
    "Region",
    "Proxy",
    "IMAP Email",
    "IMAP Password",
    "IMAP Server",
    "Notes",
    "Status",
    "Group",
    "Health",
    "Tags",
    "Created At",
    "Updated At",
]

# ── Column map: internal field → accepted header names (first match wins) ─────
ACCOUNTS_COLUMN_MAP: dict = {
    "id":           ["ID", "Id", "id", "Account ID"],
    "accountName":  ["Account Name", "account_name", "Name", "Label", "Account Label"],
    "email":        ["Email", "email", "Email Address", "Login"],
    "password":     ["Password", "password", "Pass"],
    "firstName":    ["First Name", "first_name", "FirstName"],
    "lastName":     ["Last Name", "last_name", "LastName", "Surname"],
    "phone":        ["Phone", "phone", "Phone Number", "Mobile"],
    "address1":     ["Address 1", "address1", "Address Line 1", "Street"],
    "address2":     ["Address 2", "address2", "Address Line 2"],
    "city":         ["City", "city", "Town"],
    "postcode":     ["Postcode", "postcode", "ZIP", "Postal Code"],
    "country":      ["Country", "country"],
    "region":       ["Region", "region", "State", "Province"],
    "proxy":        ["Proxy", "proxy", "Proxy URL"],
    "imapEmail":    ["IMAP Email", "imap_email", "IMAP Login"],
    "imapPassword": ["IMAP Password", "imap_password", "IMAP Pass"],
    "imapServer":   ["IMAP Server", "imap_server", "IMAP Host"],
    "notes":        ["Notes", "notes", "Comments"],
    "status":       ["Status", "status"],
    "groupTag":     ["Group", "group_tag", "Group Tag", "Tag"],
    "health":       ["Health", "health"],
    "tags":         ["Tags", "tags"],
    "createdAt":    ["Created At", "created_at", "Created"],
    "updatedAt":    ["Updated At", "updated_at", "Updated"],
}

# ── Column letter helper (same utility as used in sheets_service) ─────────────
def _col_letter(n: int) -> str:
    """Convert 1-based column index to letter(s): 1→A, 26→Z, 27→AA."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


_COL_END = _col_letter(len(ACCOUNTS_CANONICAL_HEADERS))   # e.g. "X"


# ── Shared parsers (identical to sheets_service.py) ───────────────────────────

def _safe_num(value: Any, default: float = 0) -> float:
    """European-aware numeric parser — handles €, commas as decimal, %, unicode minus."""
    if value is None or str(value).strip() in ("", "-", "—", "–"):
        return default
    s = str(value).strip()
    s = s.replace("€", "").replace("£", "").replace("$", "")
    s = s.replace("%", "").replace("\u2212", "-").replace("\u2013", "-").strip()
    last_dot   = s.rfind(".")
    last_comma = s.rfind(",")
    if last_comma > last_dot:
        s = s.replace(".", "").replace(",", ".")
    elif last_dot > last_comma:
        s = s.replace(",", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def _safe_date(value: Any) -> Optional[str]:
    """Parse a cell value to YYYY-MM-DD string or None."""
    if not value or str(value).strip() in ("", "-", "—"):
        return None
    s = str(value).strip().rstrip(".")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
                "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            d = datetime.strptime(s, fmt)
            if d.year < 1970:
                return None
            return d.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ── Singleton client cache (same pattern as sheets_service.py) ────────────────

_client = None
_sheet  = None

# One lock for all Sheets I/O — gspread's HTTP session is not thread-safe for
# concurrent writes. Serialising here costs ~0ms (ops take 500ms–2s each anyway).
_sheets_lock = threading.Lock()


def _get_client():
    global _client
    if _client:
        return _client
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        private_key = os.getenv("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n")
        if not private_key:
            raise ValueError("GOOGLE_PRIVATE_KEY env var is not set")

        creds_info = {
            "type":                        "service_account",
            "project_id":                  os.getenv("GOOGLE_PROJECT_ID", ""),
            "private_key_id":              os.getenv("GOOGLE_PRIVATE_KEY_ID", ""),
            "private_key":                 private_key,
            "client_email":                os.getenv("GOOGLE_CLIENT_EMAIL", ""),
            "token_uri":                   "https://oauth2.googleapis.com/token",
            "auth_uri":                    "https://accounts.google.com/o/oauth2/auth",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url":        "",
        }
        creds   = Credentials.from_service_account_info(creds_info, scopes=scopes)
        _client = gspread.authorize(creds)
        return _client
    except ImportError:
        raise RuntimeError("gspread and google-auth are not installed.")


def _get_spreadsheet():
    global _sheet
    if _sheet:
        return _sheet
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    if not spreadsheet_id:
        raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID env var is not set")
    _sheet = _get_client().open_by_key(spreadsheet_id)
    return _sheet


def _get_accounts_worksheet():
    """Get or auto-create the Accounts Base worksheet."""
    tab_name = os.getenv("GOOGLE_SHEETS_ACCOUNTS_TAB", "Accounts Base")
    ss = _get_spreadsheet()
    try:
        return ss.worksheet(tab_name)
    except Exception:
        # Tab doesn't exist — create it with canonical headers
        ws = ss.add_worksheet(title=tab_name, rows=2000, cols=len(ACCOUNTS_CANONICAL_HEADERS))
        ws.update("A1", [ACCOUNTS_CANONICAL_HEADERS], value_input_option="USER_ENTERED")
        try:
            ws.format(f"A1:{_COL_END}1", {"textFormat": {"bold": True}})
        except Exception:
            pass
        logger.info(f"Created new Accounts Base tab: '{tab_name}'")
        return ws


def reset_client():
    """Force re-auth on next call (use after credential rotation)."""
    global _client, _sheet
    _client = None
    _sheet  = None


# ── Header map (identical pattern to sheets_service.py) ───────────────────────

def _build_header_map(headers: list, column_map: dict) -> dict:
    header_lower = {h.strip().lower(): i for i, h in enumerate(headers)}
    result = {}
    for field, candidates in column_map.items():
        for candidate in candidates:
            if candidate.strip().lower() in header_lower:
                result[field] = header_lower[candidate.strip().lower()]
                break
    return result


# ── Row ↔ dict conversion ─────────────────────────────────────────────────────

def _row_to_account(row: list, header_map: dict, row_num: int) -> dict:
    def cell(field: str) -> str:
        idx = header_map.get(field)
        if idx is None or idx >= len(row):
            return ""
        return str(row[idx]).strip()

    return {
        "id":           cell("id"),
        "_rowNum":      row_num,
        "accountName":  cell("accountName"),
        "email":        cell("email"),
        "password":     cell("password"),
        "firstName":    cell("firstName"),
        "lastName":     cell("lastName"),
        "phone":        cell("phone"),
        "address1":     cell("address1"),
        "address2":     cell("address2"),
        "city":         cell("city"),
        "postcode":     cell("postcode"),
        "country":      cell("country"),
        "region":       cell("region"),
        "proxy":        cell("proxy"),
        "imapEmail":    cell("imapEmail"),
        "imapPassword": cell("imapPassword"),
        "imapServer":   cell("imapServer"),
        "notes":        cell("notes"),
        "status":       cell("status") or "active",
        "groupTag":     cell("groupTag"),
        "health":       cell("health") or "fresh",
        "tags":         cell("tags"),
        "createdAt":    cell("createdAt"),
        "updatedAt":    cell("updatedAt"),
    }


def _sqlite_dict_to_row(acc: dict) -> list:
    """
    Convert a SQLite account dict (snake_case) to a canonical-header row list.
    Accepts both snake_case (from SQLite) and camelCase (from API payloads).
    """
    def pick(*keys) -> str:
        for k in keys:
            v = acc.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""

    return [
        pick("id"),
        pick("account_name", "accountName"),
        pick("email"),
        pick("password"),
        pick("first_name", "firstName"),
        pick("last_name", "lastName"),
        pick("phone"),
        pick("address1"),
        pick("address2"),
        pick("city"),
        pick("postcode"),
        pick("country"),
        pick("region"),
        pick("proxy"),
        pick("imap_email", "imapEmail"),
        pick("imap_password", "imapPassword"),
        pick("imap_server", "imapServer"),
        pick("notes"),
        pick("status") or "active",
        pick("group_tag", "groupTag"),
        pick("health") or "fresh",
        pick("tags"),
        pick("created_at", "createdAt"),
        pick("updated_at", "updatedAt"),
    ]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _next_data_row(all_rows: list) -> int:
    """Return 1-indexed row number for next data insertion (after last non-blank row)."""
    for i in range(len(all_rows) - 1, 0, -1):
        if any(c.strip() for c in all_rows[i]):
            return i + 2   # +1 to convert from 0-index, +1 for next row
    return 2               # only header row exists


# ── Public API ────────────────────────────────────────────────────────────────

def getAccountsStatus() -> dict:
    """Check connectivity and return tab info."""
    try:
        ws  = _get_accounts_worksheet()
        all_rows = ws.get_all_values()
        rows = max(0, len(all_rows) - 1)
        return {
            "connected": True,
            "tab":       ws.title,
            "rows":      rows,
            "spreadsheet_id": os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", ""),
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


def syncAccountToSheets(account: dict) -> bool:
    """
    Upsert one account row in Google Sheets (by ID).
    If a row with the same ID exists → update it.
    If not found → append a new row.
    Serialised with _sheets_lock — safe to call from concurrent threads.
    Returns True on success, False on error.
    """
    with _sheets_lock:
        try:
            account_id = str(account.get("id", "")).strip()
            if not account_id:
                return False

            ws       = _get_accounts_worksheet()
            all_rows = ws.get_all_values()
            if not all_rows:
                return False

            headers    = all_rows[0]
            header_map = _build_header_map(headers, ACCOUNTS_COLUMN_MAP)
            id_col_idx = header_map.get("id", 0)
            row_data   = _sqlite_dict_to_row(account)

            # Find existing row
            target_row = None
            for i, row in enumerate(all_rows[1:], start=2):
                if len(row) > id_col_idx and str(row[id_col_idx]).strip() == account_id:
                    target_row = i
                    break

            if target_row:
                ws.update(f"A{target_row}:{_COL_END}{target_row}",
                          [row_data], value_input_option="USER_ENTERED")
            else:
                next_row = _next_data_row(all_rows)
                ws.update(f"A{next_row}:{_COL_END}{next_row}",
                          [row_data], value_input_option="USER_ENTERED")

            logger.debug(f"syncAccountToSheets: id={account_id} row={target_row or 'new'}")
            return True
        except Exception as e:
            logger.warning(f"syncAccountToSheets failed for id={account.get('id')}: {e}")
            return False


def syncAccountToSheetsBg(account: dict):
    """Fire-and-forget background sync — never blocks the API."""
    t = threading.Thread(target=syncAccountToSheets, args=(account,), daemon=True)
    t.start()


def deleteAccountFromSheets(account_id) -> bool:
    """Delete account row from Google Sheets by ID. Returns True if found+deleted."""
    with _sheets_lock:
        try:
            ws       = _get_accounts_worksheet()
            all_rows = ws.get_all_values()
            if not all_rows:
                return False

            headers    = all_rows[0]
            header_map = _build_header_map(headers, ACCOUNTS_COLUMN_MAP)
            id_col_idx = header_map.get("id", 0)
            sid        = str(account_id).strip()

            for i, row in enumerate(all_rows[1:], start=2):
                if len(row) > id_col_idx and str(row[id_col_idx]).strip() == sid:
                    ws.delete_rows(i)
                    return True
            return False
        except Exception as e:
            logger.warning(f"deleteAccountFromSheets failed for id={account_id}: {e}")
            return False


def deleteAccountFromSheetsBg(account_id):
    """Fire-and-forget background delete."""
    t = threading.Thread(target=deleteAccountFromSheets, args=(account_id,), daemon=True)
    t.start()


def bulkSyncToSheets(accounts: list) -> int:
    """
    Full overwrite sync: replace all data rows with the provided accounts list.
    Preserves the header row. Returns count of rows written.
    Serialised with _sheets_lock.
    """
    with _sheets_lock:
        try:
            ws       = _get_accounts_worksheet()
            all_rows = ws.get_all_values()
            total    = len(all_rows)

            # Clear all data rows (keep row 1 = header)
            if total > 1:
                ws.delete_rows(2, total)

            if not accounts:
                return 0

            rows    = [_sqlite_dict_to_row(a) for a in accounts]
            col_end = _col_letter(len(ACCOUNTS_CANONICAL_HEADERS))
            ws.update(f"A2:{col_end}{len(rows)+1}", rows,
                      value_input_option="USER_ENTERED")
            logger.info(f"bulkSyncToSheets: wrote {len(rows)} accounts")
            return len(rows)
        except Exception as e:
            logger.error(f"bulkSyncToSheets failed: {e}")
            return 0


def importAccountsFromSheets() -> list:
    """
    Read the Accounts Base tab and return list of dicts.
    Used for the "Import from Sheets" feature — caller merges into SQLite.
    Skips rows with no ID and no email.
    """
    try:
        ws       = _get_accounts_worksheet()
        all_rows = ws.get_all_values()
        if not all_rows:
            return []

        headers    = all_rows[0]
        header_map = _build_header_map(headers, ACCOUNTS_COLUMN_MAP)
        accounts   = []

        for i, row in enumerate(all_rows[1:], start=2):
            if not any(c.strip() for c in row):
                continue
            acc = _row_to_account(row, header_map, i)
            # Must have at least an ID or an email to be importable
            if not acc["id"] and not acc["email"]:
                continue
            accounts.append(acc)

        return accounts
    except Exception as e:
        logger.error(f"importAccountsFromSheets failed: {e}")
        return []
