"""
sheets_service.py — Google Sheets service layer for Ticket Management.

ALL Google Sheets API calls live here. Routes and UI never touch Sheets directly.
Swap this file out for a real database later without changing anything else.

Auth: Google Service Account JSON key loaded from env vars.
"""

import os
import uuid
import logging
from datetime import datetime
from typing import Optional, Any

logger = logging.getLogger(__name__)

# ── Column mapping ─────────────────────────────────────────────────────────────
# Maps internal field name → list of acceptable sheet header names (first match wins).
# Edit this list if your sheet uses different column headers.

TICKET_COLUMN_MAP: dict[str, list[str]] = {
    # ── Exact matches for ENTRADAS_MAIN_SHEET.xlsx headers (first in each list) ──
    # ── followed by common aliases so the mapping works on other sheets too ──────
    "id":                  ["ID", "Id", "id", "Ticket ID"],
    "createdAt":           ["Created At", "CreatedAt", "created_at", "Date Added", "Added"],
    "updatedAt":           ["Updated At", "UpdatedAt", "updated_at"],
    "eventName":           ["Event", "Event Name", "Artist", "Show", "event_name", "Concert"],
    "venue":               ["Venue", "venue", "Location", "Arena", "Stadium"],
    "eventDate":           ["Date", "Event Date", "Show Date", "event_date", "Concert Date"],
    "bookingRef":          ["Booking Ref", "Booking Reference", "Ref", "booking_ref",
                            "Order Number", "Order #", "Reference", "Confirmation"],
    "boughtFrom":          ["Purchased At", "Bought From", "Source", "Platform", "bought_from",
                            "Purchased From", "Purchase Source", "Seller"],
    "soldOn":              ["Sold/Listed", "Sold / Listed", "Sold On", "sold_on",
                            "Sale Platform", "Sold Platform", "Sold Via", "Resale Platform"],
    "buyerEmail":          ["Account Email", "Buyer Email", "Purchase Email", "Login Email",
                            "Email Used", "buyer_email", "Account"],
    "section":             ["Section", "section", "Block", "Stand"],
    "row":                 ["Row", "row", "Row Number"],
    "seatFrom":            ["Seat From", "Seat Start", "First Seat", "seat_from",
                            "Seats From", "Seat Number From", "From Seat"],
    "seatTo":              ["Seat To", "Seat End", "Last Seat", "seat_to",
                            "Seats To", "Seat Number To", "To Seat"],
    "ticketType":          ["Ticket Type", "Type", "ticket_type", "Category", "Tier"],
    "qtyBought":           ["Qty Bought", "Quantity", "Tickets", "No. Tickets",
                            "Num Tickets", "qty_bought", "Qty", "No of Tickets",
                            "Number of Tickets", "Tickets Bought", "# Tickets"],
    "qtySold":             ["Qty Sold", "Sold", "qty_sold", "Tickets Sold",
                            "Num Sold", "Number Sold", "# Sold"],
    "qtyUnsold":           ["Qty Unsold", "Unsold", "qty_unsold", "Remaining",
                            "Tickets Remaining", "Left", "Unsold Qty"],
    "totalCost":           ["Total Cost", "Cost", "total_cost", "Buy Price Total",
                            "Total Buy Price", "Amount Paid", "Total Paid", "Total Purchase"],
    "costPerTicket":       ["Cost Per Ticket", "Buy Price Per Ticket",
                            "cost_per_ticket", "Price Paid", "Purchase Price"],
    "salePricePerTicket":  ["Face Value", "Sale Price Per Ticket", "Sale Price", "Sell Price",
                            "sale_price_per_ticket", "Selling Price", "List Price",
                            "Sale Price Each", "Sold For"],
    "soldListed":          ["Sale Status", "Listing Status", "Status", "Listed"],
    "totalRevenue":        ["Income", "Total Revenue", "Revenue", "total_revenue",
                            "Total Sale", "Total Sold For", "Sales Total"],
    "grossProfit":         ["Profit", "Gross Profit", "gross_profit", "P&L",
                            "Profit/Loss", "Net Gain"],
    "profitMargin":        ["Profit Margin", "Margin", "profit_margin"],
    "deliveryStatus":      ["All Delivered", "Delivery Status", "Delivery", "delivery_status",
                            "Delivered", "Ticket Status", "Fulfilment"],
    "payoutStatus":        ["Paid Out", "Payout Status", "Payout", "payout_status",
                            "Payment Status", "Paid", "Payment"],
    "payoutDate":          ["Payout Date", "payout_date", "Payment Date",
                            "Paid Date", "Date Paid", "Received Date"],
    "notes":               ["Notes", "notes", "Comments", "Note", "Remarks"],
}

EXPENSES_COLUMN_MAP: dict[str, list[str]] = {
    "id":          ["ID", "Id", "id"],
    "date":        ["Date Purchased", "Date", "date", "Expense Date", "When"],
    "description": ["Purchased at", "Description", "description", "Expense", "Item", "Details",
                    "Purchased At", "Merchant", "Vendor"],
    "amount":      ["Amount", "amount", "Cost", "Value", "Price", "Total"],
    "category":    ["Category", "category", "Type", "Expense Type"],
    "eventName":   ["Event Name", "Event", "event_name", "Related Event", "Linked Event"],
    "notes":       ["Notes", "notes", "Comments"],
}


# ── Google Sheets client ───────────────────────────────────────────────────────

_client = None   # cached gspread client
_sheet  = None   # cached spreadsheet


def _get_client():
    """Return a cached authenticated gspread client."""
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
        raise RuntimeError(
            "gspread and google-auth are not installed. "
            "Run: pip install gspread google-auth"
        )


def _get_spreadsheet():
    """Return cached spreadsheet object."""
    global _sheet
    if _sheet:
        return _sheet

    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    if not spreadsheet_id:
        raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID env var is not set")

    _sheet = _get_client().open_by_key(spreadsheet_id)
    return _sheet


def _get_worksheet(tab_env_var: str, fallback_name: str):
    """Get a worksheet by env-var-configured tab name."""
    tab_name = os.getenv(tab_env_var, fallback_name)
    try:
        return _get_spreadsheet().worksheet(tab_name)
    except Exception as e:
        logger.error(f"Could not open sheet tab '{tab_name}': {e}")
        raise


def reset_client():
    """Force reconnect on next request (call after credential update)."""
    global _client, _sheet
    _client = None
    _sheet  = None


# ── Header mapping helpers ─────────────────────────────────────────────────────

def _build_header_map(headers: list[str], column_map: dict) -> dict[str, int]:
    """
    Build {internal_field: col_index} from raw sheet headers.
    Uses first match from each field's candidate list.
    Returns only fields that were found.
    """
    header_lower = {h.strip().lower(): i for i, h in enumerate(headers)}
    result = {}
    for field, candidates in column_map.items():
        for candidate in candidates:
            if candidate.strip().lower() in header_lower:
                result[field] = header_lower[candidate.strip().lower()]
                break
    return result


def _safe_num(value: Any, default=0) -> float:
    """
    Production-grade numeric parser for European spreadsheet values.

    Correctly handles:
      European decimal comma  →  286,87   = 286.87
      EU thousands dot        →  1.234,56 = 1234.56
      US thousands comma      →  1,234.56 = 1234.56
      Currency symbols        →  €286,87  = 286.87
      Percentage              →  35,58%   = 35.58  (NOT 3558)
      Unicode/typographic −   →  −146,87  = -146.87
      Plain integer           →  28687    = 28687.0

    NEVER strips commas blindly — determines their role first so that
    83,98 → 83.98 and NOT 8398.
    """
    if value is None or value == "":
        return default

    s = str(value).strip()
    if not s:
        return default

    # 1. Replace typographic / Unicode minus signs with ASCII hyphen-minus
    s = s.replace('\u2212', '-').replace('\u2014', '-').replace('\u2013', '-')

    # 2. Strip non-numeric decoration (currency, %, non-breaking spaces)
    for ch in ('€', '£', '$', '%', '\u00a0', '\u202f', '\u200b'):
        s = s.replace(ch, '')
    s = s.strip()

    if not s or s in ('-', '+', '.', ','):
        return default

    # 3. Extract sign
    negative = s.startswith('-')
    if negative:
        s = s[1:]
    elif s.startswith('+'):
        s = s[1:]

    if not s:
        return default

    try:
        last_dot   = s.rfind('.')
        last_comma = s.rfind(',')

        if last_dot == -1 and last_comma == -1:
            # Plain integer — no separators
            result = float(s)

        elif last_comma == -1:
            # Only dots present
            if s.count('.') == 1:
                result = float(s)                      # 286.87 → decimal point
            else:
                result = float(s.replace('.', ''))     # 1.234.567 → EU thousands

        elif last_dot == -1:
            # Only commas present
            if s.count(',') == 1:
                result = float(s.replace(',', '.'))    # 286,87 → decimal comma
            else:
                result = float(s.replace(',', ''))     # 1,234,567 → US thousands

        elif last_dot > last_comma:
            # Dot is rightmost → decimal point, commas = thousands  (1,234.56)
            result = float(s.replace(',', ''))

        else:
            # Comma is rightmost → decimal comma, dots = thousands  (1.234,56)
            result = float(s.replace('.', '').replace(',', '.'))

        return -result if negative else result

    except (ValueError, TypeError):
        return default


def _safe_int(value: Any, default=0) -> int:
    return int(_safe_num(value, default))


def _safe_str(value: Any, default="") -> str:
    if value is None:
        return default
    return str(value).strip()


def _safe_date(value: Any) -> Optional[str]:
    """Normalise date strings to YYYY-MM-DD or return as-is.
    Returns None for empty values and Excel zero-date artefacts (1900-01-0x).
    """
    if not value:
        return None
    s = str(value).strip().rstrip(".")   # strip trailing dot (e.g. "6.3.2025.")
    if not s:
        return None
    # Try common formats
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
                "%d.%m.%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y",
                "%-d.%-m.%Y", "%d.%m.%y"):
        try:
            parsed = datetime.strptime(s, fmt)
            # Filter Excel epoch zero-dates (serial 0/1 → 1899-12-30 or 1900-01-0x)
            if parsed.year < 1970:
                return None
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s   # return as-is if no format matched


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Boolean-style column normalisers ──────────────────────────────────────────

def _denorm_delivery(internal: str) -> str:
    """Convert internal delivery status → sheet-native text."""
    mapping = {
        "SOLD_DELIVERED":   "Sold & Delivered",
        "SOLD_UNDELIVERED": "Sold - Pending Delivery",
        "PARTIAL":          "Partial",
        "UNSOLD":           "Unsold",
        # legacy
        "DELIVERED":        "Sold & Delivered",
        "PENDING":          "Unsold",
    }
    return mapping.get((internal or "").upper(), "Unsold")


def _denorm_payout(internal: str) -> str:
    """Convert internal PAID/PARTIAL/UNPAID → sheet-native Y/Partial/N."""
    if internal == "PAID":    return "Y"
    if internal == "PARTIAL": return "Partial"
    return "N"


def _normalise_delivery(raw) -> str:
    """Map any raw sheet value to one of: UNSOLD / SOLD_UNDELIVERED / SOLD_DELIVERED / PARTIAL."""
    if raw is None:
        return "UNSOLD"
    v = str(raw).strip().lower()
    # New canonical values (pass-through)
    if v in ("unsold",):                                              return "UNSOLD"
    if v in ("sold_undelivered", "sold - pending delivery",
             "sold undelivered", "sold-undelivered"):                 return "SOLD_UNDELIVERED"
    if v in ("sold_delivered", "sold & delivered",
             "sold delivered", "sold-delivered"):                     return "SOLD_DELIVERED"
    if v in ("partial",):                                             return "PARTIAL"
    # Legacy PENDING → UNSOLD, DELIVERED → SOLD_DELIVERED
    if v in ("yes", "y", "true", "1", "delivered", "done",
             "complete", "completed", "✓", "✔"):                     return "SOLD_DELIVERED"
    if v in ("no", "n", "false", "0", "pending",
             "undelivered", "not delivered"):                         return "UNSOLD"
    return "UNSOLD"


def _normalise_payout(raw) -> str:
    """'Yes' / Y / TRUE / 'Paid' → PAID; 'Partial' → PARTIAL; anything else → UNPAID."""
    if raw is None:
        return "UNPAID"
    v = str(raw).strip().lower()
    if v in ("yes", "y", "true", "1", "paid", "paid out", "done", "✓", "✔"):
        return "PAID"
    if v in ("partial", "partly", "part paid", "partially paid", "p"):
        return "PARTIAL"
    if v in ("no", "n", "false", "0", "unpaid", "not paid", "outstanding"):
        return "UNPAID"
    if v in ("paid", "unpaid", "partial"):     # already normalised
        return v.upper()
    return "UNPAID"


def _normalise_sold_listed(raw) -> str:
    """'Yes' / TRUE / 'Sold' → SOLD; 'Listed' → LISTED; else UNLISTED."""
    if raw is None:
        return "UNLISTED"
    v = str(raw).strip().lower()
    if v in ("yes", "true", "1", "sold"):
        return "SOLD"
    if v in ("listed", "on sale", "live"):
        return "LISTED"
    if v in ("no", "false", "0", "unlisted", "not listed"):
        return "UNLISTED"
    return v.upper() if v else "UNLISTED"


# ── Row ↔ model normalisation ──────────────────────────────────────────────────

def _row_to_ticket(row: list, headers: list[str], row_num: int) -> dict:
    """Convert a raw sheet row to a typed ticket dict."""
    hmap = _build_header_map(headers, TICKET_COLUMN_MAP)

    def get(field, default=None):
        idx = hmap.get(field)
        if idx is None or idx >= len(row):
            return default
        return row[idx] if row[idx] != "" else default

    # ── Parse raw values ──────────────────────────────────────────────────────
    row_id      = _safe_str(get("id"))
    qty_bought  = _safe_int(get("qtyBought"))
    qty_sold    = _safe_int(get("qtySold"))
    total_cost  = _safe_num(get("totalCost"))
    sale_price  = _safe_num(get("salePricePerTicket"))

    # ── Derived calculations (only if not already in sheet) ───────────────────
    qty_unsold   = max(qty_bought - qty_sold, 0)
    cost_per_t   = (total_cost / qty_bought) if qty_bought > 0 else 0

    # Use sheet's Income/Revenue value directly (it's the source of truth).
    # Derive gross profit from revenue − cost; sheet's Profit column is often 0/stale.
    total_rev_raw = get("totalRevenue")
    total_rev     = _safe_num(total_rev_raw) if total_rev_raw else round(qty_sold * sale_price, 2)

    gross_p_raw = get("grossProfit")
    sheet_profit = _safe_num(gross_p_raw) if gross_p_raw else None
    # If we have both cost and revenue, always compute profit rather than trust a stale sheet value
    if total_rev != 0 or total_cost != 0:
        gross_prof = round(total_rev - total_cost, 2)
    elif sheet_profit is not None:
        gross_prof = sheet_profit
    else:
        gross_prof = 0

    # ── Stable row ID ──────────────────────────────────────────────────────────
    # Use sheet ID column if present; otherwise use row number as fallback.
    if not row_id:
        row_id = f"R{row_num}"

    return {
        "id":                 row_id,
        "_rowNum":            row_num,     # internal — used for cell updates
        "createdAt":          _safe_str(get("createdAt")),
        "updatedAt":          _safe_str(get("updatedAt")),
        "eventName":          _safe_str(get("eventName")),
        "venue":              _safe_str(get("venue")),
        "eventDate":          _safe_date(get("eventDate")),
        "bookingRef":         _safe_str(get("bookingRef")),
        "boughtFrom":         _safe_str(get("boughtFrom")),
        "buyerEmail":         _safe_str(get("buyerEmail")),
        "soldOn":             _safe_str(get("soldOn")),
        "section":            _safe_str(get("section")),
        "row":                _safe_str(get("row")),
        "seatFrom":           _safe_str(get("seatFrom")),
        "seatTo":             _safe_str(get("seatTo")),
        "ticketType":         _safe_str(get("ticketType")),
        "qtyBought":          qty_bought,
        "qtySold":            qty_sold,
        "qtyUnsold":          qty_unsold,
        "totalCost":          round(total_cost, 2),
        "costPerTicket":      round(cost_per_t, 2),
        "salePricePerTicket": round(sale_price, 2),
        "totalRevenue":       round(total_rev, 2),
        "grossProfit":        round(gross_prof, 2),
        # Profit margin = gross_profit / revenue × 100  (matches spreadsheet formula)
        # Never use the stale sheet column for this — always recompute.
        "profitMargin":       round(gross_prof / total_rev * 100, 1) if total_rev != 0
                              else (round(gross_prof / total_cost * 100, 1) if total_cost != 0 else 0.0),
        "deliveryStatus":     _normalise_delivery(get("deliveryStatus")),
        "payoutStatus":       _normalise_payout(get("payoutStatus")),
        "soldListed":         _normalise_sold_listed(get("soldListed")),
        "payoutDate":         _safe_date(get("payoutDate")),
        "notes":              _safe_str(get("notes")),
    }


def _row_to_expense(row: list, headers: list[str], row_num: int) -> dict:
    hmap = _build_header_map(headers, EXPENSES_COLUMN_MAP)

    def get(field, default=None):
        idx = hmap.get(field)
        if idx is None or idx >= len(row):
            return default
        return row[idx] if row[idx] != "" else default

    row_id = _safe_str(get("id"))
    if not row_id:
        row_id = f"E{row_num}"

    return {
        "id":          row_id,
        "_rowNum":     row_num,
        "date":        _safe_date(get("date")),
        "description": _safe_str(get("description")),
        "amount":      round(_safe_num(get("amount")), 2),
        "category":    _safe_str(get("category")),
        "eventName":   _safe_str(get("eventName")),
        "notes":       _safe_str(get("notes")),
    }


def _ensure_id_column(ws, headers: list[str]) -> list[str]:
    """
    If no ID column exists, prepend one to the sheet and return updated headers.
    Safe to call repeatedly — only acts if 'id' column is missing.
    """
    has_id = any(
        h.strip().lower() in [c.lower() for c in TICKET_COLUMN_MAP["id"]]
        for h in headers
    )
    if has_id:
        return headers

    logger.info("No ID column found — adding 'ID' column to sheet")
    ws.insert_cols([["ID"]], 1)
    return ["ID"] + headers


# ── Public API — Tickets ───────────────────────────────────────────────────────

def getTickets() -> list[dict]:
    ws      = _get_worksheet("GOOGLE_SHEETS_TICKETS_TAB", "Ticket Data")
    all_rows = ws.get_all_values()
    if not all_rows:
        return []

    headers = all_rows[0]
    hmap    = _build_header_map(headers, TICKET_COLUMN_MAP)

    # Indices of the key identity columns — used to detect truly blank rows
    _key_indices = [
        hmap.get("eventName"), hmap.get("venue"), hmap.get("bookingRef"),
        hmap.get("boughtFrom"), hmap.get("totalCost"), hmap.get("qtyBought"),
    ]
    _key_indices = [i for i in _key_indices if i is not None]

    tickets = []
    for i, row in enumerate(all_rows[1:], start=2):
        # Skip if the row has no non-empty cells at all
        if not any(cell.strip() for cell in row):
            continue
        # Skip if ALL key identity fields are empty (row is a blank template row)
        if _key_indices and not any(
            idx < len(row) and str(row[idx]).strip()
            for idx in _key_indices
        ):
            continue
        try:
            tickets.append(_row_to_ticket(row, headers, i))
        except Exception as e:
            logger.warning(f"Skipping row {i}: {e}")

    return tickets


def getTicketById(ticket_id: str) -> Optional[dict]:
    tickets = getTickets()
    for t in tickets:
        if t["id"] == ticket_id:
            return t
    return None


def createTicket(payload: dict) -> dict:
    ws      = _get_worksheet("GOOGLE_SHEETS_TICKETS_TAB", "Ticket Data")
    all_rows = ws.get_all_values()
    headers  = all_rows[0] if all_rows else []

    # Build header map to know column order
    hmap = _build_header_map(headers, TICKET_COLUMN_MAP)

    new_id  = str(uuid.uuid4())[:8].upper()
    now     = _now_iso()

    # Build a row aligned to ONLY the real data columns (A through last named header).
    # Using the full 92-column width causes Google Sheets' values.append to
    # detect a "table" that extends beyond column A and shift new rows rightward.
    # We write only the 24 real columns and use an explicit row range instead.
    num_data_cols = max((v for v in hmap.values()), default=0) + 1
    row = [""] * num_data_cols

    def set_col(field, value):
        idx = hmap.get(field)
        if idx is not None and idx < len(row):
            row[idx] = value

    # Fill known fields
    set_col("id",                 new_id)
    set_col("createdAt",          now)
    set_col("updatedAt",          now)
    set_col("eventName",          _safe_str(payload.get("eventName")))
    set_col("venue",              _safe_str(payload.get("venue")))
    set_col("eventDate",          _safe_str(payload.get("eventDate")))
    set_col("bookingRef",         _safe_str(payload.get("bookingRef")))
    set_col("boughtFrom",         _safe_str(payload.get("boughtFrom")))
    set_col("buyerEmail",         _safe_str(payload.get("buyerEmail")))
    set_col("soldOn",             _safe_str(payload.get("soldOn")))
    set_col("section",            _safe_str(payload.get("section")))
    set_col("row",                _safe_str(payload.get("row")))
    set_col("seatFrom",           _safe_str(payload.get("seatFrom")))
    set_col("seatTo",             _safe_str(payload.get("seatTo")))
    set_col("ticketType",         _safe_str(payload.get("ticketType")))
    set_col("qtyBought",          payload.get("qtyBought", 0))
    set_col("qtySold",            payload.get("qtySold", 0))
    set_col("qtyUnsold",          max(_safe_int(payload.get("qtyBought", 0)) -
                                      _safe_int(payload.get("qtySold",   0)), 0))
    set_col("totalCost",          payload.get("totalCost",          0))
    set_col("costPerTicket",      payload.get("costPerTicket",      0))
    set_col("salePricePerTicket", payload.get("salePricePerTicket", 0))
    set_col("totalRevenue",       payload.get("totalRevenue",       0))
    set_col("grossProfit",        payload.get("grossProfit",        0))
    _p_cost  = _safe_num(payload.get("totalCost", 0))
    _p_rev   = _safe_num(payload.get("totalRevenue", 0))
    _p_profit = _safe_num(payload.get("grossProfit", _p_rev - _p_cost))
    set_col("soldListed",         payload.get("soldListed", ""))
    set_col("deliveryStatus",     _denorm_delivery(payload.get("deliveryStatus", "PENDING")))
    set_col("payoutStatus",       _denorm_payout(payload.get("payoutStatus",     "UNPAID")))
    set_col("profitMargin",       round(_p_profit / _p_rev  * 100, 1) if _p_rev  != 0 else
                                  round(_p_profit / _p_cost * 100, 1) if _p_cost != 0 else 0)
    set_col("payoutDate",         _safe_str(payload.get("payoutDate")))
    set_col("notes",              _safe_str(payload.get("notes")))

    # Find the first truly empty row AFTER the last row with data in columns A-X.
    # We search from the bottom of all_rows upward to skip the blank template rows
    # that exist in the sheet between real data and the sheet's used range.
    last_data_row = 1   # 1-indexed; row 1 is the header
    for idx in range(len(all_rows) - 1, 0, -1):
        if any(str(c).strip() for c in all_rows[idx][:num_data_cols]):
            last_data_row = idx + 1   # 1-indexed row number of last real data row
            break
    new_row_num = last_data_row + 1   # write to the row immediately after

    # Write directly to the exact row range — never use append_row which can
    # mis-detect the table boundary and write to the wrong starting column.
    end_col = _col_num_to_letter(num_data_cols)
    ws.update(
        f"A{new_row_num}:{end_col}{new_row_num}",
        [row],
        value_input_option="USER_ENTERED",
    )

    return _row_to_ticket(row, headers[:num_data_cols], new_row_num)


def updateTicket(ticket_id: str, payload: dict) -> dict:
    ws       = _get_worksheet("GOOGLE_SHEETS_TICKETS_TAB", "Ticket Data")
    all_rows = ws.get_all_values()
    headers  = all_rows[0] if all_rows else []
    hmap     = _build_header_map(headers, TICKET_COLUMN_MAP)

    # Find the row
    ticket   = getTicketById(ticket_id)
    if not ticket:
        raise ValueError(f"Ticket {ticket_id} not found")

    row_num  = ticket["_rowNum"]

    # Update each provided field
    updates  = []
    now      = _now_iso()

    # Recalculate derived fields
    qty_bought  = _safe_int(payload.get("qtyBought",  ticket["qtyBought"]))
    qty_sold    = _safe_int(payload.get("qtySold",    ticket["qtySold"]))
    total_cost  = _safe_num(payload.get("totalCost",  ticket["totalCost"]))
    sale_price  = _safe_num(payload.get("salePricePerTicket", ticket["salePricePerTicket"]))
    qty_unsold  = max(qty_bought - qty_sold, 0)
    cost_per_t  = round(total_cost / qty_bought, 2) if qty_bought > 0 else 0
    total_rev   = round(qty_sold * sale_price, 2)
    gross_prof  = round(total_rev - total_cost, 2)

    profit_margin = (round(gross_prof / total_rev  * 100, 1) if total_rev  != 0 else
                     round(gross_prof / total_cost * 100, 1) if total_cost != 0 else 0.0)

    field_values = {
        **payload,
        "updatedAt":      now,
        "qtyUnsold":      qty_unsold,
        "costPerTicket":  cost_per_t,
        "totalRevenue":   total_rev,
        "grossProfit":    gross_prof,
        "profitMargin":   profit_margin,
    }

    for field, value in field_values.items():
        idx = hmap.get(field)
        if idx is None:
            continue
        # Denormalise status fields back to sheet-native format
        if field == "deliveryStatus":
            value = _denorm_delivery(str(value))
        elif field == "payoutStatus":
            value = _denorm_payout(str(value))
        # gspread uses 1-based column index
        col_letter = _col_num_to_letter(idx + 1)
        updates.append({
            "range":  f"{col_letter}{row_num}",
            "values": [[value]],
        })

    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")

    return getTicketById(ticket_id) or ticket


def updateTicketStatus(ticket_id: str, status_payload: dict) -> dict:
    """Fast update of status fields only (deliveryStatus, payoutStatus, qtySold, payoutDate)."""
    return updateTicket(ticket_id, status_payload)


def duplicateTicket(ticket_id: str) -> dict:
    """Create a copy of a ticket with a new ID, zeroed-out sales data."""
    original = getTicketById(ticket_id)
    if not original:
        raise ValueError(f"Ticket {ticket_id} not found")

    new_payload = {k: v for k, v in original.items()
                   if k not in ("id", "_rowNum", "createdAt", "updatedAt",
                                "qtySold", "qtyUnsold", "totalRevenue",
                                "grossProfit", "payoutDate", "soldOn",
                                "deliveryStatus", "payoutStatus")}
    new_payload["qtySold"]        = 0
    new_payload["deliveryStatus"] = "UNSOLD"
    new_payload["payoutStatus"]   = "UNPAID"
    new_payload["notes"]          = f"[Copy] {original.get('notes', '')}".strip()

    return createTicket(new_payload)


# ── Public API — Expenses ──────────────────────────────────────────────────────

def getExpenses() -> list[dict]:
    ws       = _get_worksheet("GOOGLE_SHEETS_EXPENSES_TAB", "Expenses")
    all_rows = ws.get_all_values()
    if not all_rows:
        return []

    headers  = all_rows[0]
    expenses = []
    for i, row in enumerate(all_rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        try:
            expenses.append(_row_to_expense(row, headers, i))
        except Exception as e:
            logger.warning(f"Skipping expense row {i}: {e}")

    return expenses


def createExpense(payload: dict) -> dict:
    ws       = _get_worksheet("GOOGLE_SHEETS_EXPENSES_TAB", "Expenses")
    all_rows = ws.get_all_values()
    headers  = all_rows[0] if all_rows else []
    hmap     = _build_header_map(headers, EXPENSES_COLUMN_MAP)

    new_id = f"EXP-{str(uuid.uuid4())[:6].upper()}"
    now    = _now_iso()
    num_data_cols = max((v for v in hmap.values()), default=0) + 1
    row    = [""] * num_data_cols

    def set_col(field, value):
        idx = hmap.get(field)
        if idx is not None and idx < len(row):
            row[idx] = value

    set_col("id",          new_id)
    set_col("date",        _safe_str(payload.get("date", now[:10])))
    set_col("description", _safe_str(payload.get("description")))
    set_col("amount",      payload.get("amount", 0))
    set_col("category",    _safe_str(payload.get("category")))
    set_col("eventName",   _safe_str(payload.get("eventName")))
    set_col("notes",       _safe_str(payload.get("notes")))

    # Find last row with data and write to the one immediately after
    last_data_row = 1
    for idx in range(len(all_rows) - 1, 0, -1):
        if any(str(c).strip() for c in all_rows[idx][:num_data_cols]):
            last_data_row = idx + 1
            break
    new_row_num = last_data_row + 1
    end_col = _col_num_to_letter(num_data_cols)
    ws.update(
        f"A{new_row_num}:{end_col}{new_row_num}",
        [row],
        value_input_option="USER_ENTERED",
    )

    return _row_to_expense(row, headers[:num_data_cols], new_row_num)


def updateExpense(expense_id: str, payload: dict) -> dict:
    ws       = _get_worksheet("GOOGLE_SHEETS_EXPENSES_TAB", "Expenses")
    all_rows = ws.get_all_values()
    headers  = all_rows[0] if all_rows else []
    hmap     = _build_header_map(headers, EXPENSES_COLUMN_MAP)

    # Find the row
    expense = None
    row_num = None
    for i, row in enumerate(all_rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        e = _row_to_expense(row, headers, i)
        if e["id"] == expense_id:
            expense = e
            row_num = i
            break

    if not expense:
        raise ValueError(f"Expense {expense_id} not found")

    updates = []
    for field, value in payload.items():
        idx = hmap.get(field)
        if idx is not None:
            col_letter = _col_num_to_letter(idx + 1)
            updates.append({
                "range":  f"{col_letter}{row_num}",
                "values": [[value]],
            })

    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")

    return {**expense, **payload}


def deleteExpense(expense_id: str) -> bool:
    """Archive by clearing the row (preserves row structure)."""
    ws       = _get_worksheet("GOOGLE_SHEETS_EXPENSES_TAB", "Expenses")
    all_rows = ws.get_all_values()
    headers  = all_rows[0] if all_rows else []

    for i, row in enumerate(all_rows[1:], start=2):
        e = _row_to_expense(row, headers, i)
        if e["id"] == expense_id:
            ws.delete_rows(i)
            return True
    return False


# ── Public API — Summary ───────────────────────────────────────────────────────

def _normalise_platform(raw: str) -> str:
    """
    Normalise resale platform names to canonical display strings.
    Handles case variations, domain suffixes, and combined entries.

    Combined entries like 'WeList (3)/Viagogo(1)' → most-mentioned platform.
    Non-sale markers like 'X', 'REFUND' → 'Other'.
    """
    if not raw:
        return "Other"

    s = raw.strip()
    v = s.lower()

    if not v:
        return "Other"

    # Junk / non-sale markers
    if v in ("x", "refund", "n/a", "-", "none", "cancel", "cancelled", "canceled"):
        return "Other"

    # Combined entry: e.g. 'WeList (3)/Viagogo(1)' — count occurrences per platform
    # and return whichever has the highest implied count.
    # Simple heuristic: parse (N) after each platform mention.
    import re as _re
    combined_match = _re.findall(r'(\w[\w. ]*?)\s*\((\d+)\)', s)
    if len(combined_match) >= 2:
        # e.g. [('WeList', '3'), ('Viagogo', '1')]
        dominant = max(combined_match, key=lambda x: int(x[1]))
        return _normalise_platform(dominant[0])

    # Standard substring matching (handles 'Viagogo.com', 'Viagogo.co.uk', etc.)
    if "welist" in v:
        return "WeList"
    if "stubhub" in v:
        return "Stubhub.ie"
    if "viagogo" in v:
        return "Viagogo"
    if "lysted" in v:
        return "Lysted"
    if "ticketmaster" in v:
        return "Ticketmaster"
    if "seatgeek" in v:
        return "SeatGeek"
    if "fansfirst" in v or "fans first" in v:
        return "FansFirst"

    # Return the original value (stripped) for any other recognisable platform
    return s


def getSummary() -> dict:
    """
    Read summary tab as-is (read-only — preserves spreadsheet formulas).
    Returns rows as a list of dicts for display.
    Also computes live aggregates from ticket data.
    """
    # Live aggregates from raw ticket data
    tickets  = getTickets()
    expenses = []
    try:
        expenses = getExpenses()
    except Exception:
        pass

    total_invested   = sum(t["totalCost"]     for t in tickets)
    total_revenue    = sum(t["totalRevenue"]   for t in tickets)
    gross_profit     = sum(t["grossProfit"]    for t in tickets)
    total_expenses   = sum(e["amount"]         for e in expenses)
    net_profit       = gross_profit - total_expenses
    qty_bought       = sum(t["qtyBought"]      for t in tickets)
    qty_sold         = sum(t["qtySold"]        for t in tickets)
    qty_unsold       = sum(t["qtyUnsold"]      for t in tickets)
    events           = len({t["eventName"]     for t in tickets if t["eventName"]})
    open_payouts     = sum(1 for t in tickets if t["payoutStatus"] in ("UNPAID", "PENDING"))
    delivered        = sum(1 for t in tickets if t["deliveryStatus"] == "SOLD_DELIVERED")
    undelivered      = sum(1 for t in tickets if t["deliveryStatus"] == "SOLD_UNDELIVERED")
    unsold           = sum(1 for t in tickets if t["deliveryStatus"] == "UNSOLD")
    partial          = sum(1 for t in tickets if t["deliveryStatus"] == "PARTIAL")

    # ── Revenue & pending payouts by platform ────────────────────────────────
    # revenue_by_platform: ALL tickets with revenue > 0, grouped by platform
    # pending_by_platform: same but only where payoutStatus != PAID
    revenue_by_platform: dict = {}   # platform → {revenue, paid, pending, tickets}
    pending_by_platform: dict = {}   # platform → pending amount (for chart)

    for t in tickets:
        revenue  = float(t.get("totalRevenue") or 0)
        platform = _normalise_platform(t.get("soldOn") or "")
        ps       = (t.get("payoutStatus") or "UNPAID").upper()

        # Revenue table (all tickets that have income)
        if revenue > 0:
            if platform not in revenue_by_platform:
                revenue_by_platform[platform] = {"revenue": 0, "paid": 0, "pending": 0, "tickets": 0}
            revenue_by_platform[platform]["revenue"] += revenue
            revenue_by_platform[platform]["tickets"] += 1
            if ps == "PAID":
                revenue_by_platform[platform]["paid"] += revenue
            else:
                revenue_by_platform[platform]["pending"] += revenue

        # Pending chart (unpaid only)
        if ps != "PAID" and revenue > 0:
            pending_by_platform[platform] = round(
                pending_by_platform.get(platform, 0) + revenue, 2
            )

    # Round revenue table values
    for p in revenue_by_platform:
        for k in ("revenue", "paid", "pending"):
            revenue_by_platform[p][k] = round(revenue_by_platform[p][k], 2)

    pending_payout_total = round(sum(pending_by_platform.values()), 2)

    # Optional: read the summary sheet tab for any formula-driven rows
    sheet_rows = []
    try:
        ws       = _get_worksheet("GOOGLE_SHEETS_SUMMARY_TAB", "Financial Summary")
        all_rows = ws.get_all_values()
        sheet_rows = [{"key": r[0], "value": r[1] if len(r) > 1 else ""}
                      for r in all_rows if r and r[0]]
    except Exception as e:
        logger.warning(f"Could not read summary tab: {e}")

    return {
        "totalInvested":      round(total_invested,  2),
        "totalRevenue":       round(total_revenue,   2),
        "grossProfit":        round(gross_profit,    2),
        "totalExpenses":      round(total_expenses,  2),
        "netProfit":          round(net_profit,      2),
        "qtyBought":          qty_bought,
        "qtySold":            qty_sold,
        "qtyUnsold":          qty_unsold,
        "totalEvents":        events,
        "openPayouts":        open_payouts,
        "delivered":          delivered,
        "undelivered":        undelivered,
        "unsold":             unsold,
        "partial":            partial,
        "roi":                round((gross_profit / total_invested * 100), 1) if total_invested > 0 else 0,
        "sheetRows":          sheet_rows,
        "payoutsByPlatform":  pending_by_platform,
        "pendingPayoutTotal": pending_payout_total,
        "revenueByPlatform":  revenue_by_platform,
    }


def getSheetsStatus() -> dict:
    """Check if Sheets connection is configured and working."""
    required = ["GOOGLE_CLIENT_EMAIL", "GOOGLE_PRIVATE_KEY", "GOOGLE_SHEETS_SPREADSHEET_ID"]
    missing  = [v for v in required if not os.getenv(v)]

    if missing:
        return {"connected": False, "error": f"Missing env vars: {', '.join(missing)}"}

    try:
        _get_spreadsheet()
        return {"connected": True, "error": None}
    except Exception as e:
        return {"connected": False, "error": str(e)}


# ── Utility ────────────────────────────────────────────────────────────────────

def _col_num_to_letter(n: int) -> str:
    """Convert 1-based column index to A1 column letter (A, B, ... Z, AA, ...)."""
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result
