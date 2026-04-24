"""
heuristic_scorer.py — Fast Python pre-filter. Zero Claude tokens spent.

Philosophy:
  Focus purely on SCARCITY + SELLOUT POTENTIAL, not artist fame.
  A 600-capacity sold-out indie show at £25 face value is a better
  opportunity than a 15,000-capacity arena show at £150.

Scoring factors (0–100):
  1. Venue capacity   (0–35 pts) — smaller = scarcer = more profitable
  2. Date window      (0–25 pts) — 3 weeks to 5 months = peak buying window
  3. TM Status        (0–25 pts) — PRESALE / ON_SALE / SOLD_OUT
  4. Face value       (0–10 pts) — neutral, not a proxy for quality
  5. TicketManager    (0–20 pts) — velocity data when API key is active (READY)

Results:
  score >= FAST_LANE  → Claude immediately + Discord within 2 min
  score >= BATCH      → hourly Claude batch
  score <  BATCH      → skipped (saves Claude cost + noise)
"""

import logging
from datetime import datetime

import config

logger = logging.getLogger(__name__)

FAST_LANE = config.FAST_LANE_SCORE        # default 70
BATCH     = config.HEURISTIC_BATCH_SCORE  # default 40


def score_event(event: dict) -> dict:
    """
    Score a single event dict 0–100.

    Returns:
        {score, fast_lane, batch, skip, signals}
    """
    score   = 0
    signals = []

    # ── 1. Venue capacity (0–35 pts) ──────────────────────────────────────────
    # Smaller venue = harder to get tickets = higher secondary premium.
    # This is the single strongest predictor of resale profit.
    # NOTE: TM API does not return real capacity — we use venue_capacity.py lookup.
    # cap=0/None means venue wasn't in our lookup table → neutral score.
    cap = event.get("capacity")
    cap = int(cap) if cap else 0

    if cap <= 0:
        score += 15  # unknown capacity — neutral, benefit of doubt
        signals.append("Venue capacity unknown")
    elif cap <= 500:
        score += 35
        signals.append(f"Tiny venue {cap:,} — maximum scarcity")
    elif cap <= 1000:
        score += 33
        signals.append(f"Intimate {cap:,} cap — very scarce")
    elif cap <= 2000:
        score += 30
        signals.append(f"Small venue {cap:,} — high scarcity")
    elif cap <= 3500:
        score += 26
        signals.append(f"Club/theatre {cap:,}")
    elif cap <= 6000:
        score += 20
        signals.append(f"Mid-size {cap:,}")
    elif cap <= 10000:
        score += 13
        signals.append(f"Arena {cap:,}")
    elif cap <= 20000:
        score += 6
        signals.append(f"Large arena {cap:,} — needs sellout to be profitable")
    else:
        score += 0
        signals.append(f"Mega venue {cap:,} — skip unless confirmed sellout")

    # ── 2. Date window (0–25 pts) ─────────────────────────────────────────────
    # Sweet spot: 3 weeks to 5 months ahead.
    # Too soon = buyers already have tickets. Too far = demand not peaked yet.
    date_str = event.get("event_date") or ""
    try:
        event_dt   = datetime.strptime(date_str[:10], "%Y-%m-%d")
        days_ahead = (event_dt - datetime.utcnow()).days

        if 21 <= days_ahead <= 45:
            score += 25
            signals.append(f"{days_ahead}d away — urgent demand pressure")
        elif 46 <= days_ahead <= 90:
            score += 25
            signals.append(f"{days_ahead}d away — peak buying window")
        elif 91 <= days_ahead <= 150:
            score += 20
            signals.append(f"{days_ahead}d away — strong window")
        elif 151 <= days_ahead <= 270:
            score += 14
            signals.append(f"{days_ahead}d away — early but worth watching")
        elif 8 <= days_ahead <= 20:
            score += 18
            signals.append(f"{days_ahead}d away — last-minute spike possible")
        elif days_ahead < 8:
            score += 4
            signals.append(f"Only {days_ahead}d away — very last minute")
        elif days_ahead > 365:
            score += 5
        else:
            score += 10
    except (ValueError, TypeError):
        score += 10  # unknown date — neutral

    # ── 3. TM Status (0–25 pts) ───────────────────────────────────────────────
    status = (event.get("status") or "").upper()
    if status == "PRESALE":
        score += 25
        signals.append("⚡ PRESALE — official early-access window, act fast")
    elif status == "ON_SALE":
        score += 15
        signals.append("On sale (official TM)")
    elif status == "SOLD_OUT":
        score += 20
        signals.append("Sold out — restock candidate, secondary prices elevated")
    elif status == "OFF_SALE":
        score += 3
        signals.append("Off sale — check if returning")

    # ── 4. Face value (0–10 pts) ──────────────────────────────────────────────
    # NOT used as an artist quality signal. Cheap tickets can be the best ROI.
    # Only used to check if a profit calculation is even possible at the min threshold.
    fv  = float(event.get("face_value_min") or 0)
    min_profit = config.MIN_NET_PROFIT_GBP
    friction   = config.FRICTION_RATE

    if fv > 0:
        # Min secondary ask needed to hit the profit threshold
        # net_profit = low_ask × (1 - friction) - face_value >= min_profit
        # → low_ask >= (face_value + min_profit) / (1 - friction)
        min_ask_needed = (fv + min_profit) / (1 - friction)
        min_multiplier = min_ask_needed / fv  # e.g. 1.8x = needs 80% above face

        if min_multiplier <= 1.5:
            score += 10
            signals.append(f"Face £{fv:.0f} — only needs {(min_multiplier-1)*100:.0f}% spread to profit")
        elif min_multiplier <= 2.0:
            score += 8
            signals.append(f"Face £{fv:.0f} — needs {(min_multiplier-1)*100:.0f}% spread")
        elif min_multiplier <= 3.0:
            score += 5
            signals.append(f"Face £{fv:.0f} — needs {(min_multiplier-1)*100:.0f}% spread (harder)")
        else:
            score += 2
            signals.append(f"Face £{fv:.0f} — high bar to hit £{min_profit:.0f} profit")
    else:
        score += 5  # unknown face value — neutral

    # ── 5. TicketManager data (0–20 pts) — READY, needs API key ──────────────
    tm_data = event.get("_tm_data")
    if tm_data:
        sell_pct  = tm_data.get("sell_through_pct") or 0
        sold_24h  = tm_data.get("sold_in_24h") or 0
        remaining = tm_data.get("remaining")
        tier      = tm_data.get("velocity_tier", "UNKNOWN")

        if sell_pct >= 80 or tier == "FAST":
            score += 20
            signals.append(f"🔥 {sell_pct:.0f}% sold — near sellout ({sold_24h} in 24h)")
        elif sell_pct >= 60:
            score += 14
            signals.append(f"{sell_pct:.0f}% sold — strong velocity")
        elif sell_pct >= 40:
            score += 8
            signals.append(f"{sell_pct:.0f}% sold — building momentum")

        if remaining is not None and remaining < 100:
            score += 10
            signals.append(f"Only {remaining} tickets left!")
        elif remaining is not None and remaining < 300:
            score += 5
            signals.append(f"{remaining} tickets remaining")

    score = max(0, min(100, score))

    return {
        "score":     score,
        "fast_lane": score >= FAST_LANE,
        "batch":     BATCH <= score < FAST_LANE,
        "skip":      score < BATCH,
        "signals":   signals,
    }


def filter_events(events: list) -> dict:
    """
    Score and split a list of events.

    Returns:
        {fast_lane: [...], batch: [...], skipped_count: int}
    """
    fast_lane = []
    batch     = []
    skipped   = 0

    for event in events:
        result = score_event(event)
        tagged = {**event, "_heuristic": result}

        if result["fast_lane"]:
            fast_lane.append(tagged)
        elif result["batch"]:
            batch.append(tagged)
        else:
            skipped += 1
            logger.debug(
                f"SKIP ({result['score']}/100): "
                f"{event.get('artist','?')} — {event.get('city','?')} "
                f"cap {event.get('capacity','?')}"
            )

    logger.info(
        f"Heuristic: {len(fast_lane)} fast-lane "
        f"| {len(batch)} batch "
        f"| {skipped} skipped "
        f"(total {len(events)})"
    )
    return {"fast_lane": fast_lane, "batch": batch, "skipped_count": skipped}
