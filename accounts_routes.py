"""
accounts_routes.py — Flask Blueprint for the Accounts Database and CSV Batch Builder.

Endpoints:
  Accounts:
    GET    /api/accounts                      list (search, filter, sort, paginate)
    POST   /api/accounts                      create
    GET    /api/accounts/<id>                 get one
    PUT    /api/accounts/<id>                 update
    DELETE /api/accounts/<id>                 delete
    POST   /api/accounts/<id>/duplicate       clone
    GET    /api/accounts/meta                 groups + countries for filter dropdowns
    GET    /api/accounts/<id>/signup-history  signup history for one account

  Batches:
    GET    /api/batches                list
    POST   /api/batches                create
    GET    /api/batches/<id>           get one (with account list)
    PUT    /api/batches/<id>           update config
    DELETE /api/batches/<id>           delete
    POST   /api/batches/<id>/accounts  set account list
    GET    /api/batches/<id>/rows      preview rows (mapped, ready for CSV)
    GET    /api/batches/<id>/export    download CSV file
    POST   /api/batches/<id>/clone     clone batch + accounts

  Proxies:
    GET    /api/proxies                list all proxies
    POST   /api/proxies                create proxy
    GET    /api/proxies/<id>           get one
    PUT    /api/proxies/<id>           update
    DELETE /api/proxies/<id>           delete
    POST   /api/proxies/<id>/test      mark tested / update status

  Batch Templates:
    GET    /api/batch-templates        list templates
    POST   /api/batch-templates        save current batch config as template
    DELETE /api/batch-templates/<id>   delete template

  Export History:
    GET    /api/export-history         list recent exports

  Signup History:
    POST   /api/signup-history/bulk    bulk log signup results

CSV column order is defined by CSV_COLUMNS — the single source of truth.
Field mapping is defined by CSV_MAPPING — one place to change column→source bindings.
"""

import csv
import io
import json
import logging
from datetime import datetime

from flask import Blueprint, jsonify, request, Response

import database as db
import accounts_sheets_service as ass

logger      = logging.getLogger(__name__)
accounts_bp = Blueprint("accounts", __name__)


def _sync_account_bg(acc_id: int):
    """Fetch the latest account from SQLite and sync to Sheets in the background."""
    try:
        acc = db.get_account(acc_id)
        if acc:
            ass.syncAccountToSheetsBg(dict(acc))
    except Exception as e:
        logger.debug(f"_sync_account_bg {acc_id}: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# CSV TEMPLATE — SINGLE SOURCE OF TRUTH
# ══════════════════════════════════════════════════════════════════════════════

# Exact column order from spec — DO NOT reorder.
CSV_COLUMNS = [
    "task_id",
    "event_url",
    "mode",
    "use_proxy",
    "number_of_tickets",
    "max_tickets",
    "min_price",
    "max_price",
    "include_resale",
    "delay_between_accounts",
    "first_name",
    "last_name",
    "email",
    "password",
    "postal_code",
    "phone_number",
    "proxy",
    "presale_code",
    "wait_queue",
    "imap_email",
    "imap_password",
    "imap_server",
    "sections",
    "aco_profile",
    "monitor_wait_time",
    "message",
    "otp_provider",
]

# Mapping: csv_column → (source, field_name)
#   source: "account" | "batch" | "const" | "index"
#   field_name: key in the account dict / batch dict / literal value / "row_index"
CSV_MAPPING = {
    "task_id":                  ("batch",   "batch_name"),   # event/batch label
    "event_url":                ("batch",   "target_url"),
    "mode":                     ("batch",   "mode"),
    "use_proxy":                ("batch",   "use_proxy"),
    "number_of_tickets":        ("batch",   "number_of_tickets"),
    "max_tickets":              ("batch",   "max_tickets"),
    "min_price":                ("batch",   "min_price"),
    "max_price":                ("batch",   "max_price"),
    "include_resale":           ("batch",   "include_resale"),
    "delay_between_accounts":   ("batch",   "delay_between_accounts"),
    "first_name":               ("account", "first_name"),
    "last_name":                ("account", "last_name"),
    "email":                    ("account", "email"),
    "password":                 ("account", "password"),
    "postal_code":              ("account", "postcode"),
    "phone_number":             ("account", "phone"),
    "proxy":                    ("account", "proxy"),
    "presale_code":             ("batch",   "presale_code"),
    "wait_queue":               ("batch",   "wait_queue"),
    "imap_email":               ("account", "imap_email"),
    "imap_password":            ("account", "imap_password"),
    "imap_server":              ("account", "imap_server"),
    "sections":                 ("batch",   "sections"),
    "aco_profile":              ("batch",   "aco_profile"),
    "monitor_wait_time":        ("batch",   "monitor_wait_time"),
    "message":                  ("batch",   "message"),
    "otp_provider":             ("batch",   "otp_provider"),
}


# Columns that must output 'true' / 'false' (not '0'/'1' or 'yes'/'no')
_BOOL_COLS = {"use_proxy", "include_resale", "wait_queue"}

_TRUTHY  = {"1", "true",  "yes", "on"}
_FALSY   = {"0", "false", "no",  "off"}

def _normalise(col: str, val: str) -> str:
    """Coerce boolean columns to 'true'/'false'; leave everything else as-is."""
    if col not in _BOOL_COLS:
        return val
    if val.lower() in _TRUTHY:
        return "true"
    if val.lower() in _FALSY:
        return "false"
    return val   # unknown — pass through


def build_csv_row(batch: dict, account: dict, overrides: dict, row_index: int) -> dict:
    """
    Build one CSV row dict (keyed by CSV_COLUMNS) from batch + account + overrides.

    Priority: override > account/batch mapping > empty string.
    All values are strings (or empty string) — never None — to preserve blank cells.
    Boolean columns (use_proxy, include_resale, wait_queue) are normalised to
    'true' / 'false' to match the bot's expected format.
    """
    row = {}
    for col in CSV_COLUMNS:
        # 1. Manual override wins
        if col in overrides and overrides[col] is not None and overrides[col] != "":
            row[col] = _normalise(col, str(overrides[col]))
            continue

        # 2. Mapping
        mapping = CSV_MAPPING.get(col)
        if mapping is None:
            row[col] = ""
            continue

        source, field = mapping
        if source == "account":
            val = account.get(field)
            row[col] = _normalise(col, str(val)) if val is not None else ""
        elif source == "batch":
            val = batch.get(field)
            row[col] = _normalise(col, str(val)) if val is not None else ""
        elif source == "const":
            row[col] = _normalise(col, str(field)) if field is not None else ""
        else:
            row[col] = ""

    return row


def generate_csv_bytes(rows: list) -> bytes:
    """
    Render a list of row dicts (from build_csv_row) as CSV bytes.
    - Exact column order from CSV_COLUMNS
    - Empty cells remain empty (no collapsing)
    - Values that look numeric but should stay as text are quoted safely
    - Deterministic output
    """
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=CSV_COLUMNS,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for row in rows:
        # Ensure every column has a value (even if empty string)
        safe_row = {col: row.get(col, "") for col in CSV_COLUMNS}
        writer.writerow(safe_row)
    return buf.getvalue().encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# ACCOUNT ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@accounts_bp.route("/api/accounts/meta")
def accounts_meta():
    """Groups and countries for filter dropdowns."""
    try:
        db.init_db()
        return jsonify({
            "groups":    db.get_account_groups(),
            "countries": db.get_account_countries(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/accounts", methods=["GET"])
def list_accounts():
    try:
        db.init_db()
        result = db.list_accounts(
            q        = request.args.get("q", ""),
            status   = request.args.get("status", ""),
            health   = request.args.get("health", ""),
            group_tag= request.args.get("group", ""),
            country  = request.args.get("country", ""),
            page     = int(request.args.get("page", 1)),
            per_page = int(request.args.get("per_page", 100)),
        )
        # Never return passwords in list view — mask them
        for acc in result["accounts"]:
            if acc.get("password"):
                acc["password"] = "••••••••"
        return jsonify(result)
    except Exception as e:
        logger.error(f"list_accounts: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/accounts", methods=["POST"])
def create_account():
    try:
        db.init_db()
        data = request.get_json() or {}
        if not data.get("email"):
            return jsonify({"error": "email is required"}), 400
        acc_id = db.create_account(data)
        _sync_account_bg(acc_id)   # mirror to Google Sheets
        return jsonify({"id": acc_id, "status": "created"}), 201
    except Exception as e:
        logger.error(f"create_account: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/accounts/<int:acc_id>", methods=["GET"])
def get_account(acc_id: int):
    try:
        db.init_db()
        acc = db.get_account(acc_id)
        if not acc:
            return jsonify({"error": "Not found"}), 404
        return jsonify(dict(acc))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/accounts/<int:acc_id>", methods=["PUT"])
def update_account(acc_id: int):
    try:
        db.init_db()
        data = request.get_json() or {}
        db.update_account(acc_id, data)
        _sync_account_bg(acc_id)   # mirror to Google Sheets
        return jsonify({"status": "updated"})
    except Exception as e:
        logger.error(f"update_account: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/accounts/<int:acc_id>", methods=["DELETE"])
def delete_account(acc_id: int):
    try:
        db.init_db()
        db.delete_account(acc_id)
        ass.deleteAccountFromSheetsBg(acc_id)   # mirror delete to Sheets
        return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/accounts/<int:acc_id>/duplicate", methods=["POST"])
def duplicate_account(acc_id: int):
    try:
        db.init_db()
        new_id = db.duplicate_account(acc_id)
        _sync_account_bg(new_id)   # mirror to Google Sheets
        return jsonify({"id": new_id, "status": "duplicated"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Bulk Edit ─────────────────────────────────────────────────────────────────

@accounts_bp.route("/api/accounts/bulk-edit", methods=["POST"])
def bulk_edit_accounts():
    """
    Bulk-update selected fields on multiple accounts.

    Body:
      {
        "account_ids":  [1, 2, 3],
        "fields": {
          "proxy":   {"value": "http://...", "mode": "replace"},
          "status":  {"value": "",           "mode": "clear"}
        },
        "dry_run": false   // optional: true = preview only, no writes
      }
    """
    try:
        db.init_db()
        payload     = request.get_json() or {}
        account_ids = payload.get("account_ids", [])
        fields      = payload.get("fields", {})
        dry_run     = bool(payload.get("dry_run", False))

        if not account_ids:
            return jsonify({"error": "account_ids is required"}), 400
        if not fields:
            return jsonify({"error": "fields is required"}), 400
        if not isinstance(account_ids, list):
            return jsonify({"error": "account_ids must be a list"}), 400

        account_ids = [int(i) for i in account_ids]

        if dry_run:
            # Preview: return what WOULD change without writing
            accounts = db.list_accounts_by_ids(account_ids)
            preview  = []
            from database import _BULK_EDITABLE_FIELDS
            safe_fields   = {}
            unsafe_fields = []
            for field, spec in fields.items():
                if field not in _BULK_EDITABLE_FIELDS:
                    unsafe_fields.append(field)
                    continue
                mode  = spec.get("mode", "replace")
                value = spec.get("value", "")
                if mode == "replace" and (value == "" or value is None):
                    continue
                safe_fields[field] = spec

            for acc in accounts:
                preview.append({
                    "id":    acc["id"],
                    "email": acc.get("email", ""),
                    "name":  acc.get("account_name", "") or acc.get("email", ""),
                    "changes": {
                        f: {
                            "from": acc.get(f, ""),
                            "to":   spec.get("value", "") if spec.get("mode") != "clear" else ""
                        }
                        for f, spec in safe_fields.items()
                    }
                })
            return jsonify({
                "dry_run":       True,
                "count":         len(accounts),
                "fields":        list(safe_fields.keys()),
                "unsafe_fields": unsafe_fields,
                "preview":       preview[:20],   # cap preview at 20 rows
            })

        # Live update
        result = db.bulk_update_accounts(account_ids, fields)

        # Background sync to Sheets.
        # For bulk operations we always do a full overwrite sync — it's 2 API
        # calls regardless of how many accounts changed, vs. N reads + N writes
        # if we loop individually (which hits rate limits and races).
        def _bulk_sync_to_sheets():
            try:
                all_accs = db.list_accounts(per_page=99999)["accounts"]
                ass.bulkSyncToSheets(all_accs)
                logger.info(f"Bulk edit sheets sync: wrote {len(all_accs)} accounts")
            except Exception as e:
                logger.warning(f"Bulk edit sheets sync failed: {e}")
        import threading
        threading.Thread(target=_bulk_sync_to_sheets, daemon=True).start()

        logger.info(
            f"Bulk edit: {result['updated']} accounts updated, "
            f"fields={result.get('fields_changed', [])}"
        )
        return jsonify(result)

    except Exception as e:
        logger.error(f"bulk_edit_accounts: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Google Sheets Sync ────────────────────────────────────────────────────────

@accounts_bp.route("/api/accounts/sync-to-sheets", methods=["POST"])
def sync_accounts_to_sheets():
    """Full overwrite sync: push all SQLite accounts to Google Sheets."""
    try:
        db.init_db()
        all_accs = db.list_accounts(per_page=99999)["accounts"]
        count    = ass.bulkSyncToSheets(all_accs)
        return jsonify({"status": "ok", "synced": count})
    except Exception as e:
        logger.error(f"sync_accounts_to_sheets: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/accounts/sync-from-sheets", methods=["POST"])
def sync_accounts_from_sheets():
    """
    Import accounts from Google Sheets into SQLite.
    - Rows with a numeric ID that matches an existing SQLite record → update
    - Rows with no matching ID → create new record
    Returns summary of imported/updated/skipped counts.
    """
    try:
        db.init_db()
        sheet_accounts = ass.importAccountsFromSheets()
        if not sheet_accounts:
            return jsonify({"status": "ok", "imported": 0, "updated": 0, "skipped": 0,
                            "message": "No accounts found in Accounts Base sheet."})

        imported = updated = skipped = 0

        for acc in sheet_accounts:
            raw_id = acc.get("id", "")
            # Try to parse as integer (our SQLite IDs)
            sqlite_id = None
            try:
                sqlite_id = int(raw_id)
            except (ValueError, TypeError):
                pass

            # Map camelCase sheet fields → snake_case SQLite fields
            data = {
                "account_name":  acc.get("accountName", ""),
                "email":         acc.get("email", ""),
                "password":      acc.get("password", ""),
                "first_name":    acc.get("firstName", ""),
                "last_name":     acc.get("lastName", ""),
                "phone":         acc.get("phone", ""),
                "address1":      acc.get("address1", ""),
                "address2":      acc.get("address2", ""),
                "city":          acc.get("city", ""),
                "postcode":      acc.get("postcode", ""),
                "country":       acc.get("country", ""),
                "region":        acc.get("region", ""),
                "proxy":         acc.get("proxy", ""),
                "imap_email":    acc.get("imapEmail", ""),
                "imap_password": acc.get("imapPassword", ""),
                "imap_server":   acc.get("imapServer", ""),
                "notes":         acc.get("notes", ""),
                "status":        acc.get("status", "active"),
                "group_tag":     acc.get("groupTag", ""),
                "health":        acc.get("health", "fresh"),
                "tags":          acc.get("tags", ""),
            }

            if not data["email"]:
                skipped += 1
                continue

            if sqlite_id:
                existing = db.get_account(sqlite_id)
                if existing:
                    db.update_account(sqlite_id, data)
                    updated += 1
                    continue

            # Create new
            try:
                db.create_account(data)
                imported += 1
            except Exception as e:
                logger.warning(f"sync_from_sheets: could not import row: {e}")
                skipped += 1

        return jsonify({
            "status":   "ok",
            "imported": imported,
            "updated":  updated,
            "skipped":  skipped,
            "total":    len(sheet_accounts),
        })
    except Exception as e:
        logger.error(f"sync_accounts_from_sheets: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/accounts/sheets-status", methods=["GET"])
def accounts_sheets_status():
    """Return Google Sheets connection status for the Accounts Base tab."""
    try:
        status = ass.getAccountsStatus()
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# BATCH ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@accounts_bp.route("/api/batches", methods=["GET"])
def list_batches():
    try:
        db.init_db()
        return jsonify(db.list_batches())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batches", methods=["POST"])
def create_batch():
    try:
        db.init_db()
        data = request.get_json() or {}
        if not data.get("batch_name"):
            return jsonify({"error": "batch_name is required"}), 400
        batch_id = db.create_batch(data)
        return jsonify({"id": batch_id, "status": "created"}), 201
    except Exception as e:
        logger.error(f"create_batch: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batches/<int:batch_id>", methods=["GET"])
def get_batch(batch_id: int):
    try:
        db.init_db()
        batch = db.get_batch(batch_id)
        if not batch:
            return jsonify({"error": "Not found"}), 404
        # Include account IDs in this batch
        with db.get_conn() as conn:
            acc_rows = conn.execute(
                "SELECT account_id FROM batch_accounts WHERE batch_id = ? ORDER BY sort_order",
                (batch_id,),
            ).fetchall()
        batch_dict = dict(batch)
        batch_dict["account_ids"] = [r["account_id"] for r in acc_rows]
        return jsonify(batch_dict)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batches/<int:batch_id>", methods=["PUT"])
def update_batch(batch_id: int):
    try:
        db.init_db()
        data = request.get_json() or {}
        db.update_batch(batch_id, data)
        return jsonify({"status": "updated"})
    except Exception as e:
        logger.error(f"update_batch: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batches/<int:batch_id>", methods=["DELETE"])
def delete_batch(batch_id: int):
    try:
        db.init_db()
        db.delete_batch(batch_id)
        return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batches/<int:batch_id>/accounts", methods=["POST"])
def set_batch_accounts(batch_id: int):
    """Replace the account list for a batch."""
    try:
        db.init_db()
        data = request.get_json() or {}
        account_ids = data.get("account_ids", [])
        db.set_batch_accounts(batch_id, account_ids)
        return jsonify({"status": "updated", "count": len(account_ids)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batches/<int:batch_id>/account-details", methods=["GET"])
def get_batch_account_details(batch_id: int):
    """Return full account details for all accounts in this batch."""
    try:
        db.init_db()
        batch = db.get_batch(batch_id)
        if not batch:
            return jsonify({"error": "Not found"}), 404
        with db.get_conn() as conn:
            rows = conn.execute(
                """SELECT a.id, a.account_name, a.email, a.first_name, a.last_name,
                          a.group_tag, a.health, a.status, a.proxy, a.imap_email,
                          a.country, ba.sort_order
                   FROM batch_accounts ba
                   JOIN accounts a ON a.id = ba.account_id
                   WHERE ba.batch_id = ?
                   ORDER BY ba.sort_order, ba.id""",
                (batch_id,),
            ).fetchall()
        accounts = [dict(r) for r in rows]
        return jsonify({
            "batch_id":   batch_id,
            "batch_name": batch["batch_name"],
            "use_proxy":  batch["use_proxy"],
            "wait_queue": batch["wait_queue"],
            "accounts":   accounts,
            "total":      len(accounts),
        })
    except Exception as e:
        logger.error(f"get_batch_account_details {batch_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batches/<int:batch_id>/rows", methods=["GET"])
def batch_preview_rows(batch_id: int):
    """
    Return the preview rows for the CSV builder table.
    Each row is a dict keyed by CSV_COLUMNS — same as what the CSV will contain.
    Passwords are NOT masked here since this is the export preview.
    """
    try:
        db.init_db()
        raw_rows = db.get_batch_rows(batch_id)
        csv_rows = [
            build_csv_row(r["batch"], r["account"], r["overrides"], r["row_index"])
            for r in raw_rows
        ]
        return jsonify({
            "columns": CSV_COLUMNS,
            "rows":    csv_rows,
        })
    except Exception as e:
        logger.error(f"batch_preview_rows: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batches/<int:batch_id>/export", methods=["GET"])
def export_batch_csv(batch_id: int):
    """Download the final CSV file."""
    try:
        db.init_db()
        batch = db.get_batch(batch_id)
        if not batch:
            return jsonify({"error": "Not found"}), 404

        raw_rows = db.get_batch_rows(batch_id)
        if not raw_rows:
            return jsonify({"error": "No accounts in this batch"}), 400

        csv_rows = [
            build_csv_row(r["batch"], r["account"], r["overrides"], r["row_index"])
            for r in raw_rows
        ]
        csv_bytes = generate_csv_bytes(csv_rows)

        batch_name = dict(batch).get("batch_name", "batch")
        safe_name  = "".join(c for c in batch_name if c.isalnum() or c in " _-").strip()
        filename   = f"{safe_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

        # Log the export to history
        try:
            db.log_export(batch_id, batch_name, len(csv_rows))
        except Exception:
            pass  # non-fatal

        return Response(
            csv_bytes,
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.error(f"export_batch_csv: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batches/<int:batch_id>/row-override", methods=["POST"])
def save_row_override(batch_id: int):
    """Save per-row cell overrides for a specific account in a batch."""
    try:
        db.init_db()
        data       = request.get_json() or {}
        account_id = data.get("account_id")
        overrides  = data.get("overrides", {})
        if not account_id:
            return jsonify({"error": "account_id required"}), 400
        db.save_batch_row_override(batch_id, account_id, overrides)
        return jsonify({"status": "saved"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _parse_colon_line(line: str) -> dict:
    """
    Parse a colon-delimited import line into an account dict.

    Supported formats (fields separated by ':'):
      2 fields:  email:password
      4 fields:  email:password:imap_email:imap_password
      6 fields:  email:password:proxy:imap_email:imap_password:imap_server

    Everything beyond 6 fields is ignored.
    Returns {} if the line looks invalid.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return {}
    parts = line.split(":")
    if len(parts) < 2:
        return {}
    result: dict = {}
    result["email"]    = parts[0].strip()
    result["password"] = parts[1].strip()
    if len(parts) >= 4:
        result["imap_email"]    = parts[2].strip()
        result["imap_password"] = parts[3].strip()
    if len(parts) >= 6:
        result["proxy"]       = parts[2].strip()
        result["imap_email"]  = parts[3].strip()
        result["imap_password"] = parts[4].strip()
        result["imap_server"]   = parts[5].strip()
    return result


@accounts_bp.route("/api/accounts/import", methods=["POST"])
def import_accounts():
    """
    Bulk import accounts.

    Accepts two formats:
      A) JSON: { "accounts": [{email, password, ...}, ...] }
      B) JSON: { "text": "email:password:proxy:imap_email:imap_pass:imap_server\\n..." }
         Lines can be 2, 4, or 6 colon-separated fields.

    Returns: { imported: N, skipped: N, errors: [...] }
    """
    try:
        db.init_db()
        data = request.get_json() or {}

        # Format B — raw text block
        raw_text = data.get("text", "")
        if raw_text:
            rows = []
            for line in raw_text.splitlines():
                parsed = _parse_colon_line(line)
                if parsed:
                    rows.append(parsed)
        else:
            rows = data.get("accounts", [])

        if not rows:
            return jsonify({"error": "No accounts provided"}), 400

        imported = 0
        skipped  = 0
        errors   = []

        for i, row in enumerate(rows):
            email = (row.get("email") or "").strip()
            if not email:
                skipped += 1
                continue
            try:
                # Upsert — update if email exists, insert otherwise
                with db.get_conn() as conn:
                    existing = conn.execute(
                        "SELECT id FROM accounts WHERE email = ?", (email,)
                    ).fetchone()

                if existing:
                    db.update_account(existing["id"], row)
                else:
                    db.create_account(row)
                imported += 1
            except Exception as e:
                errors.append(f"Row {i+1} ({email}): {e}")
                skipped += 1

        return jsonify({"imported": imported, "skipped": skipped, "errors": errors[:20]})
    except Exception as e:
        logger.error(f"import_accounts: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/accounts/stats")
def accounts_stats():
    """Quick stats for the Accounts page cards."""
    try:
        db.init_db()
        with db.get_conn() as conn:
            total    = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            active   = conn.execute("SELECT COUNT(*) FROM accounts WHERE status='active'").fetchone()[0]
            inactive = conn.execute("SELECT COUNT(*) FROM accounts WHERE status='inactive'").fetchone()[0]
            archived = conn.execute("SELECT COUNT(*) FROM accounts WHERE status='archived'").fetchone()[0]
            groups   = conn.execute(
                "SELECT COUNT(DISTINCT group_tag) FROM accounts WHERE group_tag IS NOT NULL AND group_tag != ''"
            ).fetchone()[0]
            batches  = conn.execute("SELECT COUNT(*) FROM csv_batches").fetchone()[0]
        return jsonify({
            "total": total, "active": active,
            "inactive": inactive, "archived": archived,
            "groups": groups, "batches": batches,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# CLONE BATCH
# ══════════════════════════════════════════════════════════════════════════════

@accounts_bp.route("/api/batches/<int:batch_id>/clone", methods=["POST"])
def clone_batch(batch_id: int):
    """Duplicate a batch config + all account assignments."""
    try:
        db.init_db()
        new_id = db.clone_batch(batch_id)
        return jsonify({"id": new_id, "status": "cloned"}), 201
    except Exception as e:
        logger.error(f"clone_batch: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# PROXY ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@accounts_bp.route("/api/proxies", methods=["GET"])
def list_proxies():
    try:
        db.init_db()
        status = request.args.get("status", "")
        proxies = db.list_proxies(status=status)
        return jsonify([dict(p) for p in proxies])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/proxies", methods=["POST"])
def create_proxy():
    try:
        db.init_db()
        data = request.get_json() or {}
        if not data.get("proxy_url") and not data.get("host"):
            return jsonify({"error": "proxy_url or host required"}), 400
        proxy_id = db.create_proxy(data)
        return jsonify({"id": proxy_id, "status": "created"}), 201
    except Exception as e:
        logger.error(f"create_proxy: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/proxies/<int:proxy_id>", methods=["GET"])
def get_proxy(proxy_id: int):
    try:
        db.init_db()
        proxy = db.get_proxy(proxy_id)
        if not proxy:
            return jsonify({"error": "Not found"}), 404
        return jsonify(dict(proxy))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/proxies/<int:proxy_id>", methods=["PUT"])
def update_proxy(proxy_id: int):
    try:
        db.init_db()
        data = request.get_json() or {}
        db.update_proxy(proxy_id, data)
        return jsonify({"status": "updated"})
    except Exception as e:
        logger.error(f"update_proxy: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/proxies/<int:proxy_id>", methods=["DELETE"])
def delete_proxy(proxy_id: int):
    try:
        db.init_db()
        db.delete_proxy(proxy_id)
        return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/proxies/<int:proxy_id>/test", methods=["POST"])
def test_proxy(proxy_id: int):
    """
    Receive a test result from the frontend and update proxy status.
    Body: { "status": "active" | "dead" | "slow", "notes": "..." }
    """
    try:
        db.init_db()
        data   = request.get_json() or {}
        status = data.get("status", "active")
        notes  = data.get("notes", "")
        db.set_proxy_status(proxy_id, status, notes)
        return jsonify({"status": status})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# BATCH TEMPLATE ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@accounts_bp.route("/api/batch-templates", methods=["GET"])
def list_batch_templates():
    try:
        db.init_db()
        templates = db.list_templates()
        return jsonify([dict(t) for t in templates])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batch-templates", methods=["POST"])
def create_batch_template():
    """
    Body: { "name": "...", "config": { batch config fields } }
    """
    try:
        db.init_db()
        data = request.get_json() or {}
        name   = (data.get("name") or "").strip()
        config = data.get("config", {})
        if not name:
            return jsonify({"error": "name is required"}), 400
        tpl_id = db.create_template(name, config)
        return jsonify({"id": tpl_id, "status": "created"}), 201
    except Exception as e:
        logger.error(f"create_batch_template: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batch-templates/<int:tpl_id>", methods=["GET"])
def get_batch_template(tpl_id: int):
    try:
        db.init_db()
        tpl = db.get_template(tpl_id)
        if not tpl:
            return jsonify({"error": "Not found"}), 404
        t = dict(tpl)
        # Deserialise config_json → config
        try:
            t["config"] = json.loads(t.get("config_json") or "{}")
        except Exception:
            t["config"] = {}
        return jsonify(t)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/batch-templates/<int:tpl_id>", methods=["DELETE"])
def delete_batch_template(tpl_id: int):
    try:
        db.init_db()
        db.delete_template(tpl_id)
        return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# EXPORT HISTORY
# ══════════════════════════════════════════════════════════════════════════════

@accounts_bp.route("/api/export-history", methods=["GET"])
def export_history():
    try:
        db.init_db()
        limit  = int(request.args.get("limit", 50))
        rows   = db.list_exports(limit=limit)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# SIGNUP HISTORY
# ══════════════════════════════════════════════════════════════════════════════

@accounts_bp.route("/api/accounts/<int:acc_id>/signup-history", methods=["GET"])
def account_signup_history(acc_id: int):
    try:
        db.init_db()
        rows = db.get_account_signup_history(acc_id)
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@accounts_bp.route("/api/signup-history/bulk", methods=["POST"])
def bulk_signup_history():
    """
    Log signup results in bulk and update account health.

    Body: { "results": [
        { "account_id": 1, "event_url": "...", "status": "success"|"fail"|"ban"|"error", "notes": "..." },
        ...
    ]}
    """
    try:
        db.init_db()
        data    = request.get_json() or {}
        results = data.get("results", [])
        if not results:
            return jsonify({"error": "No results provided"}), 400
        db.bulk_log_signups(results)
        return jsonify({"status": "logged", "count": len(results)})
    except Exception as e:
        logger.error(f"bulk_signup_history: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
