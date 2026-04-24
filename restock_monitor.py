"""
restock_monitor.py — Prati SOLD_OUT evente i detektuje restockove.

Logika:
  - Svaki SOLD_OUT event provjerava na TM svakih 5 minuta
  - Ako se status vrati na ON_SALE ili se pojavi ponuda → RESTOCK ALERT
  - Alert se sprema u DB i šalje na posebni Discord kanal
"""

import logging
import time
from datetime import datetime
from typing import List

import database as db
import tm_client

logger = logging.getLogger(__name__)


def check_restock(event: dict) -> bool:
    """
    Provjerava jedan SOLD_OUT event.
    Vraća True ako je restock detektovan.
    """
    tm_id      = event["tm_id"]
    event_id   = event["id"]
    prev_status = event["status"]

    current = tm_client.check_event_availability(tm_id)
    if not current:
        logger.debug(f"No TM data for {tm_id} ({event['artist']})")
        return False

    new_status = current.get("status", "ON_SALE")

    # ── Restock detektovan ─────────────────────────────────────────────────────
    if prev_status == "SOLD_OUT" and new_status in ("ON_SALE", "OFF_SALE"):
        logger.info(
            f"🔄 RESTOCK: {event['artist']} — {event['city']} "
            f"({prev_status} → {new_status})"
        )
        db.insert_restock_alert(
            event_id=event_id,
            prev_status=prev_status,
            new_status=new_status,
            tickets_appeared=None,  # TM API ne daje tačan broj
        )
        db.update_event_status(event_id, new_status)
        return True

    # ── Status se promijenio ali nije restock ──────────────────────────────────
    if new_status != prev_status:
        logger.info(f"Status change: {event['artist']} {prev_status} → {new_status}")
        db.update_event_status(event_id, new_status)

    return False


def run_restock_check() -> int:
    """
    Provjerava sve SOLD_OUT evente.
    Vraća broj detektovanih restockova.
    """
    sold_out_events = db.get_tracked_events(status_filter="SOLD_OUT")
    if not sold_out_events:
        logger.debug("No SOLD_OUT events to monitor.")
        return 0

    logger.info(f"Checking {len(sold_out_events)} SOLD_OUT events for restocks...")
    restocks = 0

    for event in sold_out_events:
        try:
            if check_restock(dict(event)):
                restocks += 1
            time.sleep(0.3)  # polite rate limiting prema TM API
        except Exception as e:
            logger.error(f"Restock check error for {event['tm_id']}: {e}")

    if restocks:
        logger.info(f"✅ {restocks} restock(s) detected!")
    else:
        logger.debug("No restocks detected this check.")

    return restocks


def run_status_update() -> int:
    """
    Ažurira status SVIH aktivnih evenata (ne samo SOLD_OUT).
    Pokreće se rjeđe (npr. svakih 60 min) za sveobuhvatni update.
    """
    all_events = db.get_tracked_events()
    updated    = 0

    logger.info(f"Status update for {len(all_events)} events...")
    for event in all_events:
        try:
            current = tm_client.check_event_availability(event["tm_id"])
            if not current:
                continue

            new_status  = current.get("status", event["status"])
            prev_status = event["status"]

            if new_status != prev_status:
                logger.info(
                    f"Update: {event['artist']} {event['city']} "
                    f"{prev_status} → {new_status}"
                )
                if prev_status == "SOLD_OUT" and new_status == "ON_SALE":
                    db.insert_restock_alert(
                        event_id=event["id"],
                        prev_status=prev_status,
                        new_status=new_status,
                    )
                db.update_event_status(event["id"], new_status)
                updated += 1

            time.sleep(0.2)
        except Exception as e:
            logger.error(f"Status update error for {event['tm_id']}: {e}")

    logger.info(f"Status update complete. {updated} events changed.")
    return updated
