"""
analyzer.py — Claude API resale scout with prompt caching.

Every event sent to Claude includes:
  - Face value + secondary market prices (if available)
  - If no secondary data: Claude estimates from its own market knowledge
  - Velocity / sell-through data (TicketManager when available, DB snapshots otherwise)
  - Historical comparables from DB (same artist + same country/venue)
  - Heuristic pre-score and signals

Claude is instructed to:
  - Only TRACK if estimated net profit >= MIN_NET_PROFIT_GBP (£40)
  - Estimate secondary prices from training knowledge when no scraped data exists
  - Explain the reasoning with specific numbers

Prompt caching: system prompt is cached for 5 minutes (saves ~90% on repeated calls).
"""

import json
import logging
import os
import re
from typing import List, Optional

import anthropic
from dotenv import load_dotenv

import config
import database as db
import velocity_tracker as vel

logger = logging.getLogger(__name__)


# ── System prompt (cached) ─────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""
You are a profit-focused ticket resale analyst for Entradas Ticket Scout (EU/UK markets).

Your job: decide whether a Ticketmaster event is worth buying official face-value tickets
to resell on secondary markets (Viagogo, StubHub, etc.) for a profit.

━━━ CORE PHILOSOPHY ━━━
Artist fame is irrelevant. A sold-out 400-capacity indie show at £28 face value
can yield better ROI than a 10,000-capacity arena show at £120.
Focus entirely on: SCARCITY × DEMAND → secondary price premium → net profit.

━━━ PROFIT REQUIREMENT ━━━
Only TRACK if estimated net profit per ticket >= £{config.MIN_NET_PROFIT_GBP:.0f} after fees.
Formula: net_profit = secondary_low_ask × (1 − {config.FRICTION_RATE}) − face_value
Fees = {config.FRICTION_RATE * 100:.0f}% (Viagogo/StubHub platform cut).

━━━ SECONDARY PRICE ESTIMATION ━━━
When no scraped secondary data exists, estimate from scarcity signals:

Scarcity signals that push prices UP:
  • Venue capacity < 1,000 → expect 2–5× face value if sold out / selling fast
  • Venue capacity 1,000–3,000 → expect 1.5–3× face value if demand is clear
  • Venue capacity 3,000–8,000 → expect 1.3–2× face value if on-sale velocity is high
  • PRESALE or limited allocation → premium of 30–80% over general-sale price
  • SOLD_OUT status → secondary prices already elevated, restock opportunity
  • Event within 6 weeks → urgency premium (+20–40% over normal)

Do NOT use artist name as the primary estimator.
Use: capacity + status + date proximity + any velocity data provided.

If you genuinely cannot estimate (no signals at all), state "insufficient data" in reason
and set verdict to IGNORE — never fabricate confidence.

━━━ TRACK CRITERIA (ALL must be true) ━━━
1. Estimated net profit >= £{config.MIN_NET_PROFIT_GBP:.0f} per ticket
2. demand_score >= 7
3. resale_potential = MEDIUM or HIGH
4. Clear scarcity signal: small venue OR sold out / near sold out OR presale
5. Event is officially on Ticketmaster (primary sale, not resale)

━━━ IGNORE CRITERIA (ANY is enough) ━━━
- Net profit < £{config.MIN_NET_PROFIT_GBP:.0f} even with generous secondary estimate
- Venue > 15,000 capacity with no sellout signals
- Artist/team known for adding unlimited extra dates (kills scarcity)
- No identifiable demand signal at all
- Event more than 18 months away (secondary market hasn't formed yet)

━━━ DEMAND SCORE GUIDE ━━━
9–10: Confirmed sellout or PRESALE with tiny capacity, prices surging
7–8:  Strong scarcity signal, clear demand, realistic £{config.MIN_NET_PROFIT_GBP:.0f}+ profit
5–6:  Some signals but uncertain — MONITOR not TRACK
1–4:  Weak or no demand signal — IGNORE

━━━ OUTPUT FORMAT ━━━
Output ONLY a valid JSON array — no markdown, no explanation:
[
  {{
    "tm_id": "...",
    "artist": "...",
    "city": "...",
    "verdict": "TRACK" | "IGNORE",
    "demand_score": 1-10,
    "resale_potential": "LOW" | "MEDIUM" | "HIGH",
    "estimated_secondary_low": 0.0,
    "estimated_net_profit": 0.0,
    "sellout_eta": "< 24h" | "2–3 days" | "1 week" | "2+ weeks" | "unknown",
    "action": "ADD_TO_BOT" | "MONITOR" | "SKIP",
    "reason": "2–3 sentences. Include: key scarcity signal used, estimated secondary price and its basis, exact net profit figure, decisive factor."
  }}
]
"""


# ── Comparables from DB ────────────────────────────────────────────────────────

def _get_comparables(artist: str, venue: str, country: str) -> str:
    """
    Pull similar past events from the DB to give Claude historical context.
    Checks: (1) same artist, (2) same venue, (3) same country + category.
    """
    lines = []
    try:
        with db.get_conn() as conn:
            # Same artist — most useful signal
            artist_rows = conn.execute("""
                SELECT e.artist, e.city, e.country, e.event_date, e.capacity,
                       e.face_value_min, a.demand_score, a.resale_potential,
                       a.verdict, a.reason,
                       s.secondary_low, s.spread_pct, s.net_profit_est
                FROM events e
                JOIN analysis a ON a.event_id = e.id
                LEFT JOIN (
                    SELECT event_id,
                           MAX(ts) as latest,
                           secondary_low, spread_pct, net_profit_est
                    FROM snapshots GROUP BY event_id
                ) s ON s.event_id = e.id
                WHERE e.artist LIKE ?
                ORDER BY a.ts DESC
                LIMIT 4
            """, (f"%{artist[:20]}%",)).fetchall()

            if artist_rows:
                lines.append(f"Past events — SAME ARTIST ({artist}):")
                for r in artist_rows:
                    profit_str = f"£{r['net_profit_est']:.0f} net" if r["net_profit_est"] else ""
                    spread_str = f"+{r['spread_pct']*100:.0f}%" if r["spread_pct"] else ""
                    low_str    = f"£{r['secondary_low']:.0f} secondary" if r["secondary_low"] else "(no price data)"
                    lines.append(
                        f"  [{r['verdict']}] {r['city']}, {r['country']} "
                        f"({r['event_date']}) cap {r['capacity'] or '?':,} "
                        f"score {r['demand_score']}/10 — {low_str} {spread_str} {profit_str}"
                    )

            # Same venue — secondary signal
            venue_rows = conn.execute("""
                SELECT e.artist, e.city, e.event_date, e.capacity,
                       a.demand_score, a.resale_potential, a.verdict,
                       s.secondary_low, s.spread_pct
                FROM events e
                JOIN analysis a ON a.event_id = e.id
                LEFT JOIN (
                    SELECT event_id, MAX(ts) as latest,
                           secondary_low, spread_pct
                    FROM snapshots GROUP BY event_id
                ) s ON s.event_id = e.id
                WHERE e.venue = ? AND e.artist != ?
                ORDER BY a.ts DESC
                LIMIT 3
            """, (venue, artist)).fetchall()

            if venue_rows:
                lines.append(f"Past events — SAME VENUE ({venue}):")
                for r in venue_rows:
                    spread_str = f"+{r['spread_pct']*100:.0f}%" if r["spread_pct"] else ""
                    lines.append(
                        f"  [{r['verdict']}] {r['artist']} ({r['event_date']}) "
                        f"score {r['demand_score']}/10 {spread_str}"
                    )

    except Exception as e:
        logger.debug(f"Comparable lookup error: {e}")

    return "\n".join(lines) if lines else "No comparable events in DB yet (first run)."


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_prompt(events_data: List[dict]) -> str:
    load_dotenv(override=True)
    friction = float(os.getenv("FRICTION_RATE", str(config.FRICTION_RATE)))
    min_profit = float(os.getenv("MIN_NET_PROFIT_GBP", str(config.MIN_NET_PROFIT_GBP)))

    lines = [f"Analyse these {len(events_data)} event(s). Min net profit required: £{min_profit:.0f}\n"]

    for i, e in enumerate(events_data, 1):
        lines.append(f"{'─'*50}")
        lines.append(f"[{i}] tm_id={e.get('tm_id','?')}")
        lines.append(f"Artist:   {e.get('artist','?')}")
        lines.append(f"Location: {e.get('city','?')}, {e.get('country','?')}")
        lines.append(f"Date:     {e.get('event_date','?')}")
        lines.append(f"Venue:    {e.get('venue','?')}")

        cap = e.get("capacity")
        lines.append(f"Capacity: {cap:,}" if cap else "Capacity: unknown")
        lines.append(f"Category: {e.get('category','music')}")
        lines.append(f"TM Status:{e.get('status','ON_SALE')}")

        # Face value
        fv_min = e.get("face_value_min")
        fv_max = e.get("face_value_max")
        curr   = e.get("currency", "GBP")
        if fv_min:
            fv_str = f"{curr} {fv_min:.2f}"
            if fv_max and fv_max != fv_min:
                fv_str += f" – {fv_max:.2f}"
            lines.append(f"Face Value: {fv_str}")
        else:
            lines.append("Face Value: unknown")

        # Secondary market
        sec = e.get("secondary")
        if sec and sec.get("low_ask") and sec.get("source") != "estimated":
            lines.append(f"Secondary Market ({sec['source'].upper()}):")
            lines.append(f"  Lowest ask:  £{sec['low_ask']:.2f}")
            if sec.get("median_ask"):
                lines.append(f"  Median ask:  £{sec['median_ask']:.2f}")
            spread = sec.get("spread_pct", 0) * 100
            net    = sec.get("net_profit_est", 0)
            conf   = sec.get("confidence", "MEDIUM")
            lines.append(f"  Gross spread: +{spread:.0f}%")
            lines.append(
                f"  Net profit est (after {friction*100:.0f}% fees): £{net:.2f}  [{conf} confidence]"
            )
            if sec.get("listings"):
                lines.append(f"  Listings visible: {sec['listings']}")
        elif sec and sec.get("source") == "estimated":
            lines.append(
                "Secondary Market: no live data — mathematical estimate only (LOW confidence). "
                "Please estimate from your knowledge of this artist."
            )
        else:
            lines.append(
                "Secondary Market: no data available. "
                "You MUST estimate the secondary low ask from your knowledge of this artist/event."
            )

        # Velocity / TicketManager
        tm_data = e.get("_tm_data")
        if tm_data:
            lines.append(tm_data.to_claude_context() if hasattr(tm_data, "to_claude_context")
                         else f"TicketManager: sell-through {tm_data.get('sell_through_pct','?')}%")
        vel_str = e.get("velocity")
        if vel_str:
            lines.append(f"Price velocity: {vel_str}")

        # Heuristic signals
        heuristic = e.get("_heuristic")
        if heuristic:
            lines.append(
                f"Heuristic pre-score: {heuristic['score']}/100 "
                f"({'fast-lane' if heuristic['fast_lane'] else 'batch'})"
            )
            if heuristic.get("signals"):
                lines.append("  Signals: " + " · ".join(heuristic["signals"][:4]))

        # Comparables
        comp_str = e.get("comparables")
        if comp_str:
            lines.append(comp_str)

        lines.append("")

    return "\n".join(lines)


# ── Response parser ────────────────────────────────────────────────────────────

def _parse_response(raw: str) -> List[dict]:
    cleaned = raw.strip()
    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        parts   = cleaned.split("\n")
        cleaned = "\n".join(parts[1:-1])

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to extract JSON array from surrounding text
        match = re.search(r'\[.*\]', cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
        logger.error(f"JSON parse failed. Raw (first 500):\n{raw[:500]}")
        return []


# ── Core analysis function ─────────────────────────────────────────────────────

def analyse_batch(event_ids: List[int], fast_lane: bool = False) -> List[dict]:
    """
    Analyse a list of event IDs from the DB.
    Returns only TRACK events that meet the £{MIN_NET_PROFIT_GBP} threshold.

    fast_lane=True: smaller batches, no batching delay — called immediately
                    after discovery for high-heuristic-score events.
    """
    if not event_ids:
        return []

    load_dotenv(override=True)
    api_key     = os.getenv("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY)
    model       = os.getenv("CLAUDE_MODEL", config.CLAUDE_MODEL)
    min_profit  = float(os.getenv("MIN_NET_PROFIT_GBP", str(config.MIN_NET_PROFIT_GBP)))
    friction    = float(os.getenv("FRICTION_RATE", str(config.FRICTION_RATE)))

    if not api_key or "xxx" in api_key.lower():
        logger.warning("ANTHROPIC_API_KEY not configured — skipping analysis.")
        return []

    client = anthropic.Anthropic(api_key=api_key)

    # ── Build event context ────────────────────────────────────────────────────
    events_data = []
    for eid in event_ids:
        event = db.get_event_by_id(eid)
        if not event:
            continue
        event = dict(event)

        # Last secondary market snapshot
        last_snap = db.get_last_snapshot(eid)
        if last_snap and last_snap.get("secondary_low"):
            event["secondary"] = {
                "source":         last_snap.get("secondary_source") or "unknown",
                "low_ask":        last_snap["secondary_low"],
                "median_ask":     last_snap.get("secondary_median"),
                "high_ask":       last_snap.get("secondary_high"),
                "spread_pct":     last_snap.get("spread_pct") or 0,
                "net_profit_est": last_snap.get("net_profit_est") or 0,
                "listings":       last_snap.get("secondary_listings"),
                "confidence":     "MEDIUM",
            }

        # Velocity from DB snapshots
        vel_report         = vel.analyse_velocity(eid)
        event["velocity"]  = vel.format_for_claude(vel_report)

        # Comparables from DB
        event["comparables"] = _get_comparables(
            event.get("artist", ""),
            event.get("venue", ""),
            event.get("country", ""),
        )

        # TicketManager data if available
        try:
            import ticketmanager_client as tmc
            if tmc.is_enabled():
                tm_data = tmc.get_event_data(event.get("tm_id", ""))
                if tm_data:
                    event["_tm_data"] = tm_data
        except Exception:
            pass

        events_data.append(event)

    if not events_data:
        return []

    mode = "FAST-LANE" if fast_lane else "BATCH"
    logger.info(
        f"[{mode}] Sending {len(events_data)} events to Claude ({model})..."
    )

    # ── Call Claude with prompt caching on system prompt ──────────────────────
    try:
        message = client.messages.create(
            model=model,
            max_tokens=4096,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # caches system prompt ~5 min
            }],
            messages=[{
                "role":    "user",
                "content": _build_prompt(events_data),
            }],
        )
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return []

    raw     = message.content[0].text
    results = _parse_response(raw)

    if not results:
        return []

    # ── Map results back to event IDs ──────────────────────────────────────────
    tm_to_id  = {str(e.get("tm_id", "")): e["id"] for e in events_data}
    profitable = []

    for idx, r in enumerate(results):
        tm_id    = str(r.get("tm_id", ""))
        event_id = tm_to_id.get(tm_id)
        if not event_id and idx < len(events_data):
            event_id = events_data[idx]["id"]  # positional fallback

        # Store in DB
        if event_id:
            db.insert_analysis(event_id, {
                "verdict":          r.get("verdict"),
                "demand_score":     r.get("demand_score"),
                "resale_potential": r.get("resale_potential"),
                "sellout_eta":      r.get("sellout_eta"),
                "velocity_trend":   (events_data[idx]["velocity"] if idx < len(events_data) else "")[:200],
                "reason":           r.get("reason"),
            })

        # ── Profit filter — don't alert on unprofitable events ────────────────
        if r.get("verdict") != "TRACK":
            continue

        est_profit = r.get("estimated_net_profit") or 0
        if est_profit > 0 and est_profit < min_profit:
            logger.info(
                f"FILTERED OUT: {r.get('artist')} — "
                f"£{est_profit:.0f} net profit below £{min_profit:.0f} threshold"
            )
            continue

        # Enrich with event data for Discord
        ev = next((e for e in events_data if e.get("id") == event_id), None)
        if ev:
            r["event_id"]   = event_id
            r["artist"]     = ev.get("artist")
            r["city"]       = ev.get("city")
            r["country"]    = ev.get("country")
            r["event_date"] = ev.get("event_date")
            r["venue"]      = ev.get("venue")
            r["tm_url"]     = ev.get("tm_url")
            r["face_value_min"] = ev.get("face_value_min")
            r["face_value_max"] = ev.get("face_value_max")
            r["currency"]   = ev.get("currency", "GBP")
            r["secondary"]  = ev.get("secondary") or {
                "source":         "claude_estimate",
                "low_ask":        r.get("estimated_secondary_low", 0),
                "spread_pct":     ((r.get("estimated_secondary_low", 0) / ev.get("face_value_min", 1)) - 1)
                                  if ev.get("face_value_min") else 0,
                "net_profit_est": r.get("estimated_net_profit", 0),
                "confidence":     "MEDIUM",
            }

        profitable.append(r)

    logger.info(
        f"[{mode}] Analysis: {len(results)} events → "
        f"{len(profitable)} flagged TRACK (above £{min_profit:.0f} threshold)"
    )
    return profitable
