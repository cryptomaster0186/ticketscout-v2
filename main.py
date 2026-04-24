"""
main.py — Entradas Ticket Scout orchestrator + CLI.

Scheduler intervals (counter-based, no external dependencies):
  Every  2 min  → fast-lane check (new high-score events → immediate Claude)
  Every  5 min  → restock check
  Every 15 min  → price snapshots (secondary market)
  Every 60 min  → full TM discovery
  Every 65 min  → batch Claude analysis (remaining events not caught by fast-lane)
  Every 12 h    → Discord status summary

Fast-lane pipeline (new):
  Discovery → heuristic_scorer → score >= 70 → immediate Claude → immediate Discord
  Everything else waits for the 65-min batch.

CLI commands:
  python main.py run        — 24/7 scheduler
  python main.py discover   — one-shot discovery
  python main.py snapshot   — one-shot price fetch
  python main.py restock    — one-shot restock check
  python main.py analyse    — one-shot Claude batch
  python main.py status     — print DB stats
  python main.py --dry-run  — no Discord sends
"""

import argparse
import logging
import sys
import time
from datetime import datetime

import config
import database as db
import tm_client
import secondary_market as sm
import velocity_tracker as vel
import restock_monitor
import analyzer
import discord_alerts
import heuristic_scorer

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("ticketscout.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── Fast-lane queue (event IDs waiting for immediate Claude) ───────────────────
_fast_lane_queue: set = set()


# ── Task: Discovery ────────────────────────────────────────────────────────────

def task_discovery(dry_run: bool = False) -> list:
    """
    Discover events via Ticketmaster. Upserts all into DB.
    Returns list of event dicts for all discovered events (used for heuristic filter).
    """
    logger.info("=" * 55)
    logger.info("🔍 TASK: Discovery")

    events = tm_client.discover_all()
    for e in events:
        try:
            db.upsert_event(e)
        except Exception as err:
            logger.debug(f"Upsert error: {err}")

    logger.info(f"Discovery done. {len(events)} events processed.")

    # Return all unanalysed ON_SALE events for heuristic scoring
    unanalysed = db.get_events_needing_analysis(hours_since_last=6)
    return [dict(e) for e in unanalysed]


# ── Task: Heuristic pre-filter + fast-lane trigger ────────────────────────────

def task_heuristic_filter(new_events: list, dry_run: bool = False):
    """
    Score new events with heuristic_scorer.
    Fast-lane events (score >= FAST_LANE_SCORE) are added to _fast_lane_queue
    for immediate Claude analysis on the next tick.
    """
    if not new_events:
        return

    result = heuristic_scorer.filter_events(new_events)

    fast = result["fast_lane"]
    if fast:
        logger.info(
            f"⚡ FAST-LANE: {len(fast)} events queued for immediate analysis"
        )
        for ev in fast:
            _fast_lane_queue.add(ev["id"])
            h = ev.get("_heuristic", {})
            logger.info(
                f"  Fast-lane: {ev.get('artist','?')} — {ev.get('city','?')} "
                f"score {h.get('score','?')}/100"
            )


# ── Task: Fast-lane Claude analysis ───────────────────────────────────────────

def task_fast_lane(dry_run: bool = False):
    """
    Immediately analyse all events in the fast-lane queue.
    Sends Discord alert right away if TRACK.
    Called every 2 minutes.
    """
    if not _fast_lane_queue:
        return

    ids = list(_fast_lane_queue)
    _fast_lane_queue.clear()

    logger.info(f"⚡ FAST-LANE analysis: {len(ids)} events")
    results = analyzer.analyse_batch(ids, fast_lane=True)

    if results and not dry_run:
        discord_alerts.send_track_alerts(results, scan_count=len(ids))
    elif dry_run:
        for r in results:
            logger.info(
                f"[DRY-RUN FAST-LANE] TRACK: {r.get('artist')} — {r.get('city')} "
                f"| score {r.get('demand_score')}/10 "
                f"| est profit £{r.get('estimated_net_profit',0):.0f}"
            )


# ── Task: Snapshots ────────────────────────────────────────────────────────────

def task_snapshot(dry_run: bool = False):
    """Fetch secondary market prices for all ON_SALE events and save snapshots."""
    logger.info("=" * 55)
    logger.info("📸 TASK: Snapshots")

    events = db.get_tracked_events(status_filter="ON_SALE")
    if not events:
        logger.info("No ON_SALE events to snapshot.")
        return

    logger.info(f"Taking snapshots for {len(events)} events...")
    done = 0

    for event in events:
        try:
            face_value = float(event.get("face_value_min") or 40.0)
            sec = sm.get_secondary_data(
                artist=event["artist"],
                city=event["city"],
                event_date=event["event_date"],
                face_value=face_value,
                demand_signals={"capacity": event.get("capacity", 5000)},
            )

            if sec.low_ask > 0:
                spread_pct = (sec.low_ask / face_value) - 1
                db.insert_snapshot(event["id"], {
                    "tm_status":          event.get("status"),
                    "secondary_source":   sec.source,
                    "secondary_low":      sec.low_ask,
                    "secondary_median":   sec.median_ask,
                    "secondary_high":     sec.high_ask,
                    "secondary_listings": sec.listings,
                    "spread_pct":         spread_pct,
                    "net_profit_est":     sec.net_profit_est,
                })
                done += 1

        except Exception as e:
            logger.error(f"Snapshot error for {event.get('artist','?')}: {e}")

    logger.info(f"Snapshots done: {done}/{len(events)} saved.")


# ── Task: Restock check ────────────────────────────────────────────────────────

def task_restock(dry_run: bool = False):
    """Check SOLD_OUT events for restocks. Sends Discord alert immediately if found."""
    logger.info("=" * 55)
    logger.info("🔄 TASK: Restock check")

    restocks = restock_monitor.run_restock_check()
    if restocks > 0 and not dry_run:
        alerts = db.get_unsent_restock_alerts()
        if alerts:
            discord_alerts.send_restock_alerts([dict(a) for a in alerts])
            for alert in alerts:
                db.mark_restock_sent(alert["id"])


# ── Task: Batch Claude analysis ────────────────────────────────────────────────

def task_analyse(dry_run: bool = False):
    """
    Hourly Claude analysis of events that:
    - Have not been analysed in the last 6h
    - Were NOT already caught by the fast-lane
    """
    logger.info("=" * 55)
    logger.info("🧠 TASK: Claude batch analysis")

    events_to_analyse = db.get_events_needing_analysis(hours_since_last=6)
    if not events_to_analyse:
        logger.info("No events need analysis right now.")
        return

    # Skip any IDs still in fast-lane queue (they'll be handled there)
    event_ids = [
        e["id"] for e in events_to_analyse
        if e["id"] not in _fast_lane_queue
    ]

    # Apply heuristic pre-filter — skip events with score < BATCH threshold
    # to avoid sending clearly unprofitable events to Claude
    events_full = [dict(db.get_event_by_id(eid)) for eid in event_ids if db.get_event_by_id(eid)]
    filtered = heuristic_scorer.filter_events(events_full)
    eligible_ids = (
        [e["id"] for e in filtered["fast_lane"]] +
        [e["id"] for e in filtered["batch"]]
    )

    if not eligible_ids:
        logger.info(
            f"Heuristic skipped all {len(event_ids)} events — "
            f"none above score {config.HEURISTIC_BATCH_SCORE}."
        )
        return

    total = len(eligible_ids)
    logger.info(f"Batch analysing {total} events (after heuristic filter)...")

    results = []
    for i in range(0, total, 15):  # batch of 15 to manage cost
        batch   = eligible_ids[i:i + 15]
        partial = analyzer.analyse_batch(batch, fast_lane=False)
        results.extend(partial)

    if not dry_run:
        discord_alerts.send_track_alerts(results, scan_count=total)
    else:
        logger.info(f"[DRY-RUN] Would send {len(results)} TRACK alerts")
        for r in results:
            logger.info(
                f"  TRACK: [{r.get('demand_score')}/10] "
                f"{r.get('artist')} — {r.get('city')} "
                f"| {r.get('resale_potential')} "
                f"| £{r.get('estimated_net_profit', 0):.0f} net"
            )


# ── Task: Status update ────────────────────────────────────────────────────────

def task_status_update():
    stats = db.get_stats()
    logger.info(
        f"📊 Status: {stats['total_events']} events | "
        f"{stats['on_sale']} on sale | {stats['sold_out']} sold out | "
        f"{stats['tracked']} tracked"
    )
    discord_alerts.send_status_update(stats)


# ── Scheduler ──────────────────────────────────────────────────────────────────

def run_scheduler(dry_run: bool = False):
    """
    Main 24/7 loop.

    Tick = 60s
    ┌────────────┬──────────────────────────────────────────────────┐
    │ Every 2t   │ Fast-lane: immediate Claude for hot new events    │
    │ Every 5t   │ Restock check (SOLD_OUT → ON_SALE detection)      │
    │ Every 15t  │ Price snapshots (secondary market)                │
    │ Every 60t  │ TM Discovery + heuristic filter                   │
    │ Every 65t  │ Batch Claude analysis                             │
    │ Every 720t │ Discord status summary                            │
    └────────────┴──────────────────────────────────────────────────┘
    """
    TICK         = 60
    tick_counter = 0

    logger.info("=" * 55)
    logger.info("🚀 Entradas Ticket Scout — Scheduler started")
    logger.info(f"   Dry-run:      {dry_run}")
    logger.info(f"   DB:           {config.DB_PATH}")
    logger.info(f"   Countries:    {', '.join(config.TM_COUNTRIES)}")
    logger.info(f"   Min profit:   £{config.MIN_NET_PROFIT_GBP:.0f}")
    logger.info(f"   Fast-lane ≥:  {config.FAST_LANE_SCORE}/100")
    logger.info(f"   Batch ≥:      {config.HEURISTIC_BATCH_SCORE}/100")
    logger.info("=" * 55)

    # Startup: immediate discovery + filter
    try:
        new_events = task_discovery(dry_run)
        task_heuristic_filter(new_events, dry_run)
    except Exception as e:
        logger.error(f"Startup discovery failed: {e}")

    while True:
        tick_counter += 1
        logger.debug(f"Tick {tick_counter}")

        try:
            # Every 2 min — fast-lane (immediate Claude for hot events)
            if tick_counter % 2 == 0:
                task_fast_lane(dry_run)

            # Every 5 min — restock check
            if tick_counter % 5 == 0:
                task_restock(dry_run)

            # Every 15 min — price snapshots
            if tick_counter % 15 == 0:
                task_snapshot(dry_run)

            # Every 60 min — TM discovery + heuristic filter
            if tick_counter % 60 == 0:
                new_events = task_discovery(dry_run)
                task_heuristic_filter(new_events, dry_run)

            # Every 65 min (5 min after discovery) — batch Claude analysis
            if tick_counter % 60 == 5:
                task_analyse(dry_run)

            # Every 12h — Discord status summary
            if tick_counter % 720 == 0:
                task_status_update()

        except KeyboardInterrupt:
            logger.info("Shutdown requested.")
            break
        except Exception as e:
            logger.error(f"Task error at tick {tick_counter}: {e}", exc_info=True)

        time.sleep(TICK)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Entradas Ticket Scout — EU/UK resale intelligence",
    )
    parser.add_argument(
        "command",
        choices=["run", "discover", "snapshot", "restock", "analyse", "status"],
        default="run",
        nargs="?",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without sending Discord alerts",
    )
    args = parser.parse_args()

    db.init_db()

    if args.command != "status" and not args.dry_run:
        try:
            config.validate()
        except EnvironmentError as e:
            logger.error(str(e))
            sys.exit(1)

    commands = {
        "run":      lambda: run_scheduler(args.dry_run),
        "discover": lambda: task_discovery(args.dry_run),
        "snapshot": lambda: task_snapshot(args.dry_run),
        "restock":  lambda: task_restock(args.dry_run),
        "analyse":  lambda: task_analyse(args.dry_run),
        "status":   lambda: logger.info(str(db.get_stats())),
    }

    try:
        commands[args.command]()
    except KeyboardInterrupt:
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
