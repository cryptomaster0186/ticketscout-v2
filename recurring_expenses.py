"""
recurring_expenses.py — Recurring monthly expense rules engine.

Architecture:
  - Recurring rules are stored ONE ROW PER RULE in the "Recurring Expenses"
    Google Sheet tab (configured via GOOGLE_SHEETS_RECURRING_EXPENSES_TAB).
  - Calendar occurrences are DERIVED DYNAMICALLY from those rules — we never
    create duplicate rows for future months.
  - Historical expenses (one-time) remain in the existing "Expenses" tab;
    this module does NOT touch that tab.

Recurrence logic:
  - A rule fires on `dayOfMonth` of every month between startDate and endDate.
  - If a month has fewer days than `dayOfMonth` (e.g. day=31 in February),
    the occurrence falls on the last valid day of that month.
  - If a rule has isActive=false, cancelledAt, or endDate before the target
    month, it generates NO occurrences for that month.
  - Past months where the rule WAS active still show historically correct data
    via getCalendarOccurrences(year, month) — it computes retroactively too.
"""

import calendar
import logging
import os
import uuid
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Column map for "Recurring Expenses" sheet tab ─────────────────────────────

RECURRING_COLUMN_MAP: dict = {
    "id":            ["ID", "Id", "id"],
    "name":          ["Name", "name", "Description", "Expense Name"],
    "amount":        ["Amount", "amount", "Cost", "Value"],
    "currency":      ["Currency", "currency"],
    "category":      ["Category", "category", "Type"],
    "dayOfMonth":    ["Day Of Month", "Day", "day_of_month", "Due Day"],
    "startDate":     ["Start Date", "start_date", "From"],
    "endDate":       ["End Date", "end_date", "Until", "Expires"],
    "isActive":      ["Is Active", "is_active", "Active"],
    "status":        ["Status", "status"],
    "vendor":        ["Vendor", "vendor", "Supplier"],
    "paymentMethod": ["Payment Method", "payment_method", "Payment"],
    "colorTag":      ["Color Tag", "color_tag", "Color"],
    "notes":         ["Notes", "notes", "Comments"],
    "cancelledAt":   ["Cancelled At", "cancelled_at", "Cancelled"],
    "createdAt":     ["Created At", "created_at"],
    "updatedAt":     ["Updated At", "updated_at"],
}

# Canonical headers used when we auto-create the sheet tab
CANONICAL_HEADERS = [
    "ID", "Name", "Amount", "Currency", "Category",
    "Day Of Month", "Start Date", "End Date", "Is Active", "Status",
    "Vendor", "Payment Method", "Color Tag", "Notes",
    "Cancelled At", "Created At", "Updated At",
]


# ── Google Sheets helpers (reuse sheets_service internals) ────────────────────

def _get_worksheet():
    """Return the Recurring Expenses worksheet, creating it if needed."""
    import sheets_service as ss
    tab_name = os.getenv("GOOGLE_SHEETS_RECURRING_EXPENSES_TAB", "Recurring Expenses")
    spreadsheet = ss._get_spreadsheet()
    try:
        return spreadsheet.worksheet(tab_name)
    except Exception:
        # Auto-create the tab with headers
        ws = spreadsheet.add_worksheet(title=tab_name, rows=500, cols=len(CANONICAL_HEADERS))
        ws.update("A1", [CANONICAL_HEADERS])
        logger.info(f"Auto-created '{tab_name}' sheet tab")
        return ws


def _build_hmap(headers: list) -> dict:
    from sheets_service import _build_header_map
    return _build_header_map(headers, RECURRING_COLUMN_MAP)


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _col_letter(col_idx: int) -> str:
    """Convert 1-based column index to letter (A, B, …, Z, AA, …)."""
    result = ""
    while col_idx > 0:
        col_idx, rem = divmod(col_idx - 1, 26)
        result = chr(65 + rem) + result
    return result


def _parse_date(value) -> Optional[date]:
    """Safely parse a date string (YYYY-MM-DD or DD/MM/YYYY or similar)."""
    if not value or str(value).strip() in ("", "None", "null"):
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _safe_bool(value, default=True) -> bool:
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "yes", "1", "active"):
        return True
    if s in ("false", "no", "0", "inactive", "cancelled", ""):
        return False
    return default


def _safe_num(value, default=0.0) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        s = str(value).strip().replace(",", ".").replace("€", "").replace("£", "").replace("$", "")
        return float(s)
    except (ValueError, TypeError):
        return default


def _row_to_rule(row: list, headers: list, row_num: int) -> dict:
    """Convert a sheet row to a recurring expense rule dict."""
    hmap = _build_hmap(headers)

    def get(field, default=""):
        idx = hmap.get(field)
        if idx is None or idx >= len(row):
            return default
        v = row[idx]
        return v if v is not None else default

    rule_id = get("id") or f"REC-{row_num}"
    is_active = _safe_bool(get("isActive", "true"), True)
    status = get("status") or ("active" if is_active else "inactive")

    return {
        "id":            rule_id,
        "name":          get("name"),
        "amount":        _safe_num(get("amount")),
        "currency":      get("currency") or "EUR",
        "category":      get("category"),
        "dayOfMonth":    int(_safe_num(get("dayOfMonth"), 1)),
        "startDate":     get("startDate"),
        "endDate":       get("endDate"),
        "isActive":      is_active,
        "status":        status,
        "vendor":        get("vendor"),
        "paymentMethod": get("paymentMethod"),
        "colorTag":      get("colorTag"),
        "notes":         get("notes"),
        "cancelledAt":   get("cancelledAt"),
        "createdAt":     get("createdAt"),
        "updatedAt":     get("updatedAt"),
        "_row":          row_num,
    }


# ── Public API — CRUD ─────────────────────────────────────────────────────────

def getRecurringExpenses() -> list:
    """Return all recurring expense rules (active and inactive)."""
    ws = _get_worksheet()
    all_rows = ws.get_all_values()
    if not all_rows:
        return []
    headers = all_rows[0]
    rules = []
    for i, row in enumerate(all_rows[1:], start=2):
        if not any(str(c).strip() for c in row):
            continue
        try:
            rules.append(_row_to_rule(row, headers, i))
        except Exception as e:
            logger.warning(f"Skipping recurring expense row {i}: {e}")
    return rules


def createRecurringExpense(payload: dict) -> dict:
    """Create a new recurring expense rule."""
    ws = _get_worksheet()
    all_rows = ws.get_all_values()
    if not all_rows:
        ws.update("A1", [CANONICAL_HEADERS])
        all_rows = ws.get_all_values()
    headers = all_rows[0]
    hmap = _build_hmap(headers)

    new_id = f"REC-{str(uuid.uuid4())[:6].upper()}"
    now = _now_iso()
    num_cols = len(headers)
    row = [""] * num_cols

    def set_col(field, value):
        idx = hmap.get(field)
        if idx is not None and idx < len(row):
            row[idx] = str(value) if value is not None else ""

    is_active = payload.get("isActive", True)
    status = payload.get("status", "active" if is_active else "inactive")

    set_col("id",            new_id)
    set_col("name",          payload.get("name", ""))
    set_col("amount",        payload.get("amount", 0))
    set_col("currency",      payload.get("currency", "EUR"))
    set_col("category",      payload.get("category", ""))
    set_col("dayOfMonth",    payload.get("dayOfMonth", 1))
    set_col("startDate",     payload.get("startDate", date.today().isoformat()))
    set_col("endDate",       payload.get("endDate", ""))
    set_col("isActive",      "true" if is_active else "false")
    set_col("status",        status)
    set_col("vendor",        payload.get("vendor", ""))
    set_col("paymentMethod", payload.get("paymentMethod", ""))
    set_col("colorTag",      payload.get("colorTag", ""))
    set_col("notes",         payload.get("notes", ""))
    set_col("cancelledAt",   "")
    set_col("createdAt",     now)
    set_col("updatedAt",     now)

    # Find last data row
    last_data_row = 1
    for idx in range(len(all_rows) - 1, 0, -1):
        if any(str(c).strip() for c in all_rows[idx][:num_cols]):
            last_data_row = idx + 1
            break
    new_row_num = last_data_row + 1
    end_col = _col_letter(num_cols)
    ws.update(
        f"A{new_row_num}:{end_col}{new_row_num}",
        [row],
        value_input_option="USER_ENTERED",
    )

    return _row_to_rule(row, headers[:num_cols], new_row_num)


def updateRecurringExpense(rule_id: str, payload: dict) -> dict:
    """Update fields of an existing recurring expense rule."""
    ws = _get_worksheet()
    all_rows = ws.get_all_values()
    if not all_rows:
        raise ValueError(f"Recurring expense {rule_id} not found")
    headers = all_rows[0]
    hmap = _build_hmap(headers)

    rule = None
    row_num = None
    for i, row in enumerate(all_rows[1:], start=2):
        if not any(str(c).strip() for c in row):
            continue
        r = _row_to_rule(row, headers, i)
        if r["id"] == rule_id:
            rule = r
            row_num = i
            break

    if not rule:
        raise ValueError(f"Recurring expense {rule_id} not found")

    now = _now_iso()
    updates = []

    field_map = {
        "name": "name", "amount": "amount", "currency": "currency",
        "category": "category", "dayOfMonth": "dayOfMonth",
        "startDate": "startDate", "endDate": "endDate",
        "isActive": "isActive", "status": "status",
        "vendor": "vendor", "paymentMethod": "paymentMethod",
        "colorTag": "colorTag", "notes": "notes", "cancelledAt": "cancelledAt",
    }

    for py_field, sheet_field in field_map.items():
        if py_field in payload:
            idx = hmap.get(sheet_field)
            if idx is not None:
                val = payload[py_field]
                if isinstance(val, bool):
                    val = "true" if val else "false"
                updates.append({
                    "range": f"{_col_letter(idx + 1)}{row_num}",
                    "values": [[str(val) if val is not None else ""]],
                })

    # Always update updatedAt
    idx = hmap.get("updatedAt")
    if idx is not None:
        updates.append({"range": f"{_col_letter(idx + 1)}{row_num}", "values": [[now]]})

    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")

    return {**rule, **payload, "updatedAt": now}


def toggleRecurringExpense(rule_id: str) -> dict:
    """Toggle isActive for a rule. Sets cancelledAt if deactivating."""
    rules = getRecurringExpenses()
    rule = next((r for r in rules if r["id"] == rule_id), None)
    if not rule:
        raise ValueError(f"Recurring expense {rule_id} not found")

    new_active = not rule["isActive"]
    updates = {
        "isActive": new_active,
        "status":   "active" if new_active else "cancelled",
    }
    if not new_active and not rule.get("cancelledAt"):
        updates["cancelledAt"] = date.today().isoformat()
    elif new_active:
        updates["cancelledAt"] = ""

    return updateRecurringExpense(rule_id, updates)


def deleteRecurringExpense(rule_id: str) -> bool:
    """Delete a recurring expense rule row."""
    ws = _get_worksheet()
    all_rows = ws.get_all_values()
    if not all_rows:
        return False
    headers = all_rows[0]
    for i, row in enumerate(all_rows[1:], start=2):
        if not any(str(c).strip() for c in row):
            continue
        r = _row_to_rule(row, headers, i)
        if r["id"] == rule_id:
            ws.delete_rows(i)
            return True
    return False


# ── Recurrence engine ─────────────────────────────────────────────────────────

def _clamp_day(year: int, month: int, day: int) -> int:
    """Return the last valid day of the month if day > max days."""
    max_day = calendar.monthrange(year, month)[1]
    return min(day, max_day)


def _rule_active_in_month(rule: dict, year: int, month: int) -> bool:
    """
    Return True if the rule should produce an occurrence in (year, month).

    Rules:
    1. isActive must be True.
    2. startDate must be <= last day of target month.
    3. endDate (if set) must be >= first day of target month.
    4. cancelledAt (if set) must be >= first day of target month.
       (cancelled mid-month still fires that month, gone next month)
    """
    if not rule.get("isActive", True):
        return False

    first_of_month = date(year, month, 1)
    last_of_month  = date(year, month, calendar.monthrange(year, month)[1])

    start = _parse_date(rule.get("startDate"))
    if start and start > last_of_month:
        return False  # not started yet

    end = _parse_date(rule.get("endDate"))
    if end and end < first_of_month:
        return False  # already expired

    cancelled = _parse_date(rule.get("cancelledAt"))
    if cancelled and cancelled < first_of_month:
        return False  # was cancelled before this month started

    return True


def getCalendarOccurrences(year: int, month: int) -> list:
    """
    Generate all recurring expense occurrences for the given year/month.
    Returns a list of occurrence dicts sorted by day.
    """
    rules = getRecurringExpenses()
    occurrences = []

    for rule in rules:
        if not _rule_active_in_month(rule, year, month):
            continue

        day_raw = int(rule.get("dayOfMonth") or 1)
        actual_day = _clamp_day(year, month, day_raw)
        due_date = date(year, month, actual_day)

        occurrences.append({
            "ruleId":        rule["id"],
            "name":          rule["name"],
            "amount":        rule["amount"],
            "currency":      rule.get("currency", "EUR"),
            "category":      rule.get("category", ""),
            "vendor":        rule.get("vendor", ""),
            "paymentMethod": rule.get("paymentMethod", ""),
            "colorTag":      rule.get("colorTag", ""),
            "notes":         rule.get("notes", ""),
            "dayOfMonth":    day_raw,
            "day":           actual_day,
            "date":          due_date.isoformat(),
            "isRecurring":   True,
            "isActive":      rule.get("isActive", True),
            "dayClamped":    actual_day != day_raw,  # True if month was shorter
        })

    occurrences.sort(key=lambda x: x["day"])
    return occurrences


def getUpcomingExpenses(days: int = 30) -> list:
    """
    Return recurring expense occurrences for the next `days` days starting today.
    Groups by date.
    """
    today = date.today()
    rules = getRecurringExpenses()
    upcoming = []

    # Determine the year/months we need to scan
    months_to_check = set()
    from datetime import timedelta
    end_date = today + timedelta(days=days)
    current = date(today.year, today.month, 1)
    while current <= end_date:
        months_to_check.add((current.year, current.month))
        # Advance to next month
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    for (year, month) in sorted(months_to_check):
        occurrences = getCalendarOccurrences(year, month)
        for occ in occurrences:
            occ_date = _parse_date(occ["date"])
            if occ_date and today <= occ_date <= end_date:
                delta = (occ_date - today).days
                occ["daysUntil"] = delta
                occ["dueLabel"] = (
                    "Today" if delta == 0
                    else "Tomorrow" if delta == 1
                    else f"In {delta} days"
                )
                upcoming.append(occ)

    upcoming.sort(key=lambda x: x["date"])
    return upcoming


def getMonthlyTotal(year: int, month: int) -> float:
    """Sum of all recurring expense amounts for a given month."""
    return sum(occ["amount"] for occ in getCalendarOccurrences(year, month))
