"""
velocity_tracker.py — Analizira brzinu prodaje karata iz snapshot historije.

Core logika:
  - Prati promjene secondary_low cijene između snapshotova
  - Detektuje demand spike (nagli rast cijene)
  - Kalkuliše trend (ACCELERATING / STABLE / COOLING)
  - Procjenjuje sellout ETA na osnovu price velocity
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

import sqlite3

import database as db

logger = logging.getLogger(__name__)


@dataclass
class VelocityReport:
    event_id:          int
    trend:             str          # ACCELERATING | STABLE | COOLING | INSUFFICIENT_DATA
    price_change_pct:  Optional[float]   # % promjena low ask u zadnjih 24h
    avg_spread_pct:    Optional[float]   # prosječni spread u zadnjih N snapshotova
    spread_trend:      str          # RISING | FLAT | FALLING
    snapshots_count:   int
    sellout_signal:    bool         # True ako znaci su zabrinjavajući
    summary:           str          # kratko opisno


def analyse_velocity(event_id: int) -> VelocityReport:
    """
    Analizira velocity za jedan event na osnovu snapshotova u DB.
    """
    snapshots = db.get_recent_snapshots(event_id, limit=20)

    if not snapshots:
        return VelocityReport(
            event_id=event_id,
            trend="INSUFFICIENT_DATA",
            price_change_pct=None,
            avg_spread_pct=None,
            spread_trend="FLAT",
            snapshots_count=0,
            sellout_signal=False,
            summary="Nema snapshot historije.",
        )

    snaps = list(reversed(snapshots))  # sort oldest→newest

    # Filter snapshotove koji imaju secondary prices
    with_prices = [s for s in snaps if s["secondary_low"] is not None]

    if len(with_prices) < 2:
        return VelocityReport(
            event_id=event_id,
            trend="INSUFFICIENT_DATA",
            price_change_pct=None,
            avg_spread_pct=None,
            spread_trend="FLAT",
            snapshots_count=len(snaps),
            sellout_signal=False,
            summary=f"Nedovoljno price datapoints ({len(with_prices)}).",
        )

    # ── Price trend ────────────────────────────────────────────────────────────
    oldest_price  = with_prices[0]["secondary_low"]
    newest_price  = with_prices[-1]["secondary_low"]
    price_change  = ((newest_price - oldest_price) / oldest_price) * 100 if oldest_price else 0

    # ── Spread trend ───────────────────────────────────────────────────────────
    spreads = [s["spread_pct"] for s in with_prices if s["spread_pct"] is not None]
    avg_spread = sum(spreads) / len(spreads) if spreads else None

    if len(spreads) >= 3:
        first_half  = spreads[:len(spreads) // 2]
        second_half = spreads[len(spreads) // 2:]
        avg_first   = sum(first_half) / len(first_half)
        avg_second  = sum(second_half) / len(second_half)
        if avg_second > avg_first * 1.05:
            spread_trend = "RISING"
        elif avg_second < avg_first * 0.95:
            spread_trend = "FALLING"
        else:
            spread_trend = "FLAT"
    else:
        spread_trend = "FLAT"

    # ── Overall trend ──────────────────────────────────────────────────────────
    if price_change > 15:
        trend = "ACCELERATING"
    elif price_change > 5:
        trend = "RISING"
    elif price_change < -10:
        trend = "COOLING"
    else:
        trend = "STABLE"

    # ── Sellout signal ─────────────────────────────────────────────────────────
    # Signal ako: cijena raste brzo ILI spread je visok ILI ima SOLD_OUT checkova
    sold_out_snaps = sum(1 for s in snaps if s["tm_status"] == "SOLD_OUT")
    sellout_signal = (
        price_change > 20
        or (avg_spread is not None and avg_spread > 0.5)
        or sold_out_snaps > 0
        or spread_trend == "RISING"
    )

    # ── Summary ────────────────────────────────────────────────────────────────
    summary_parts = []
    if price_change != 0:
        direction = "↑" if price_change > 0 else "↓"
        summary_parts.append(f"Cijena {direction}{abs(price_change):.1f}% u {len(with_prices)} checkova")
    if avg_spread is not None:
        summary_parts.append(f"Avg spread: {avg_spread * 100:.0f}%")
    if spread_trend != "FLAT":
        summary_parts.append(f"Spread trend: {spread_trend}")
    if sold_out_snaps:
        summary_parts.append(f"{sold_out_snaps}x SOLD_OUT detektovan")

    summary = " | ".join(summary_parts) if summary_parts else "Stabilan, nema jasnog signala."

    return VelocityReport(
        event_id=event_id,
        trend=trend,
        price_change_pct=round(price_change, 2),
        avg_spread_pct=round(avg_spread, 4) if avg_spread else None,
        spread_trend=spread_trend,
        snapshots_count=len(snaps),
        sellout_signal=sellout_signal,
        summary=summary,
    )


def format_for_claude(report: VelocityReport) -> str:
    """
    Formatira velocity report u tekst koji ide u Claude prompt.
    """
    if report.trend == "INSUFFICIENT_DATA":
        return "Velocity: nema dovoljno historijskih podataka."

    lines = [
        f"Velocity trend: {report.trend}",
        f"Price change (historija): {report.price_change_pct:+.1f}%",
        f"Spread trend: {report.spread_trend}",
        f"Avg spread: {(report.avg_spread_pct or 0) * 100:.0f}%",
        f"Sellout signal: {'DA ⚠️' if report.sellout_signal else 'NE'}",
        f"Detaljno: {report.summary}",
    ]
    return "\n".join(lines)
