"""
ticket_routes.py — Flask routes for the Ticket Management module.

All routes call sheets_service functions.
No Google Sheets logic lives here.
"""

import logging
from datetime import date
from flask import Blueprint, jsonify, request
import sheets_service as ss
import recurring_expenses as re_svc

logger   = logging.getLogger(__name__)
ticket_bp = Blueprint("tickets", __name__)


# ── Connection status ──────────────────────────────────────────────────────────

@ticket_bp.route("/api/tickets/status")
def sheets_status():
    return jsonify(ss.getSheetsStatus())


# ── Tickets CRUD ───────────────────────────────────────────────────────────────

@ticket_bp.route("/api/tickets", methods=["GET"])
def list_tickets():
    try:
        tickets = ss.getTickets()

        # ── Server-side filtering ─────────────────────────────────────────────
        q            = (request.args.get("q") or "").lower().strip()
        event_filter = (request.args.get("event") or "").lower().strip()
        venue_filter = (request.args.get("venue") or "").lower().strip()
        delivery_f   = (request.args.get("delivery") or "").strip()
        payout_f     = (request.args.get("payout") or "").strip()
        sold_f       = (request.args.get("sold") or "").strip()    # all/sold/partial/unsold
        upcoming_f   = (request.args.get("upcoming") or "").strip()  # "1" = event date >= today

        if q:
            tickets = [t for t in tickets if q in (t.get("eventName","") + t.get("venue","") +
                       t.get("bookingRef","") + t.get("section","")).lower()]
        if event_filter:
            tickets = [t for t in tickets if event_filter in t.get("eventName","").lower()]
        if venue_filter:
            tickets = [t for t in tickets if venue_filter in t.get("venue","").lower()]
        if delivery_f:
            tickets = [t for t in tickets if t.get("deliveryStatus","").upper() == delivery_f.upper()]
        if payout_f:
            tickets = [t for t in tickets if t.get("payoutStatus","").upper() == payout_f.upper()]
        if sold_f == "sold":
            tickets = [t for t in tickets if t.get("qtySold",0) > 0 and t.get("qtyUnsold",0) == 0]
        elif sold_f == "partial":
            tickets = [t for t in tickets if 0 < t.get("qtySold",0) < t.get("qtyBought",0)]
        elif sold_f == "unsold":
            tickets = [t for t in tickets if t.get("qtySold",0) == 0]

        if upcoming_f == "1":
            today = date.today().isoformat()   # date already imported at top of file
            tickets = [t for t in tickets if (t.get("eventDate") or "") >= today]

        # ── Sorting ───────────────────────────────────────────────────────────
        sort_by  = request.args.get("sort", "eventDate")
        sort_dir = request.args.get("dir", "asc")
        reverse  = sort_dir == "desc"

        def sort_key(t):
            v = t.get(sort_by, "")
            if v is None:
                return ""
            return str(v).lower()

        tickets.sort(key=sort_key, reverse=reverse)

        # ── Pagination ────────────────────────────────────────────────────────
        per_page = int(request.args.get("per_page", 50))
        page     = int(request.args.get("page", 1))
        total    = len(tickets)
        start    = (page - 1) * per_page
        end      = start + per_page

        return jsonify({
            "tickets":    tickets[start:end],
            "total":      total,
            "page":       page,
            "per_page":   per_page,
            "pages":      max(1, -(-total // per_page)),   # ceiling div
        })
    except Exception as e:
        logger.error(f"GET /api/tickets error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/tickets", methods=["POST"])
def create_ticket():
    try:
        payload = request.get_json(force=True) or {}
        ticket  = ss.createTicket(payload)
        return jsonify(ticket), 201
    except Exception as e:
        logger.error(f"POST /api/tickets error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/tickets/<ticket_id>", methods=["GET"])
def get_ticket(ticket_id):
    try:
        ticket = ss.getTicketById(ticket_id)
        if not ticket:
            return jsonify({"error": "Not found"}), 404
        return jsonify(ticket)
    except Exception as e:
        logger.error(f"GET /api/tickets/{ticket_id} error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/tickets/<ticket_id>", methods=["PUT"])
def update_ticket(ticket_id):
    try:
        payload = request.get_json(force=True) or {}
        ticket  = ss.updateTicket(ticket_id, payload)
        return jsonify(ticket)
    except Exception as e:
        logger.error(f"PUT /api/tickets/{ticket_id} error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/tickets/<ticket_id>/status", methods=["PATCH"])
def update_ticket_status(ticket_id):
    try:
        payload = request.get_json(force=True) or {}
        ticket  = ss.updateTicketStatus(ticket_id, payload)
        return jsonify(ticket)
    except Exception as e:
        logger.error(f"PATCH /api/tickets/{ticket_id}/status error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/tickets/<ticket_id>/duplicate", methods=["POST"])
def duplicate_ticket(ticket_id):
    try:
        ticket = ss.duplicateTicket(ticket_id)
        return jsonify(ticket), 201
    except Exception as e:
        logger.error(f"POST /api/tickets/{ticket_id}/duplicate error: {e}")
        return jsonify({"error": str(e)}), 500


# ── Expenses CRUD ──────────────────────────────────────────────────────────────

@ticket_bp.route("/api/expenses", methods=["GET"])
def list_expenses():
    try:
        expenses = ss.getExpenses()
        q = (request.args.get("q") or "").lower().strip()
        if q:
            expenses = [e for e in expenses
                        if q in (e.get("description","") + e.get("eventName","") +
                                 e.get("category","")).lower()]
        return jsonify({"expenses": expenses, "total": len(expenses)})
    except Exception as e:
        logger.error(f"GET /api/expenses error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/expenses", methods=["POST"])
def create_expense():
    try:
        payload = request.get_json(force=True) or {}
        expense = ss.createExpense(payload)
        return jsonify(expense), 201
    except Exception as e:
        logger.error(f"POST /api/expenses error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/expenses/<expense_id>", methods=["PUT"])
def update_expense(expense_id):
    try:
        payload = request.get_json(force=True) or {}
        expense = ss.updateExpense(expense_id, payload)
        return jsonify(expense)
    except Exception as e:
        logger.error(f"PUT /api/expenses/{expense_id} error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/expenses/<expense_id>", methods=["DELETE"])
def delete_expense(expense_id):
    try:
        ok = ss.deleteExpense(expense_id)
        return jsonify({"deleted": ok})
    except Exception as e:
        logger.error(f"DELETE /api/expenses/{expense_id} error: {e}")
        return jsonify({"error": str(e)}), 500


# ── Recurring Expenses ────────────────────────────────────────────────────────

@ticket_bp.route("/api/expenses/recurring", methods=["GET"])
def list_recurring_expenses():
    try:
        status_filter = request.args.get("status", "")  # active / inactive / all
        rules = re_svc.getRecurringExpenses()
        if status_filter == "active":
            rules = [r for r in rules if r.get("isActive")]
        elif status_filter == "inactive":
            rules = [r for r in rules if not r.get("isActive")]
        return jsonify({"rules": rules, "total": len(rules)})
    except Exception as e:
        logger.error(f"GET /api/expenses/recurring error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/expenses/recurring", methods=["POST"])
def create_recurring_expense():
    try:
        payload = request.get_json(force=True) or {}
        rule = re_svc.createRecurringExpense(payload)
        return jsonify(rule), 201
    except Exception as e:
        logger.error(f"POST /api/expenses/recurring error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/expenses/recurring/<rule_id>", methods=["PUT"])
def update_recurring_expense(rule_id):
    try:
        payload = request.get_json(force=True) or {}
        rule = re_svc.updateRecurringExpense(rule_id, payload)
        return jsonify(rule)
    except Exception as e:
        logger.error(f"PUT /api/expenses/recurring/{rule_id} error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/expenses/recurring/<rule_id>/toggle", methods=["PATCH"])
def toggle_recurring_expense(rule_id):
    try:
        rule = re_svc.toggleRecurringExpense(rule_id)
        return jsonify(rule)
    except Exception as e:
        logger.error(f"PATCH /api/expenses/recurring/{rule_id}/toggle error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/expenses/recurring/<rule_id>", methods=["DELETE"])
def delete_recurring_expense(rule_id):
    try:
        ok = re_svc.deleteRecurringExpense(rule_id)
        return jsonify({"deleted": ok})
    except Exception as e:
        logger.error(f"DELETE /api/expenses/recurring/{rule_id} error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/expenses/calendar", methods=["GET"])
def get_expenses_calendar():
    try:
        today = date.today()
        year  = int(request.args.get("year",  today.year))
        month = int(request.args.get("month", today.month))
        if not (1 <= month <= 12):
            return jsonify({"error": "month must be 1–12"}), 400
        occurrences = re_svc.getCalendarOccurrences(year, month)
        monthly_total = sum(o["amount"] for o in occurrences)
        return jsonify({
            "year":         year,
            "month":        month,
            "occurrences":  occurrences,
            "monthlyTotal": round(monthly_total, 2),
            "count":        len(occurrences),
        })
    except Exception as e:
        logger.error(f"GET /api/expenses/calendar error: {e}")
        return jsonify({"error": str(e)}), 500


@ticket_bp.route("/api/expenses/upcoming", methods=["GET"])
def get_upcoming_expenses():
    try:
        days = int(request.args.get("days", 30))
        days = max(1, min(days, 365))
        upcoming = re_svc.getUpcomingExpenses(days)
        total = sum(o["amount"] for o in upcoming)
        return jsonify({
            "upcoming":    upcoming,
            "total":       round(total, 2),
            "count":       len(upcoming),
            "days":        days,
        })
    except Exception as e:
        logger.error(f"GET /api/expenses/upcoming error: {e}")
        return jsonify({"error": str(e)}), 500


# ── Summary ────────────────────────────────────────────────────────────────────

@ticket_bp.route("/api/tm-summary", methods=["GET"])
def get_summary():
    try:
        summary = ss.getSummary()
        return jsonify(summary)
    except Exception as e:
        logger.error(f"GET /api/tm-summary error: {e}")
        return jsonify({"error": str(e)}), 500
