"""
ticketmanager_client.py — TicketManager API integration.

STATUS: READY — waiting for API key.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW TO ACTIVATE (when you get your key):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Add to .env:
       TM_MANAGER_API_KEY=your_key_here
       TM_MANAGER_BASE_URL=https://api.ticketmanager.com/v1   ← confirm with provider
       TM_MANAGER_ENABLED=true

2. That's it. The system will automatically:
   - Pull real sales velocity (sold_in_24h, remaining, sell_through_pct)
   - Feed this into heuristic_scorer.py (the "_tm_data" block)
   - Score events much more accurately — near-sellout events jump to fast-lane
   - Replace the mathematical velocity estimate in velocity_tracker.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT IT PROVIDES (replaces estimates):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  sold_in_24h       — tickets sold in last 24h (velocity signal)
  remaining         — tickets left (scarcity signal)
  capacity          — real venue capacity
  on_sale_date      — when general sale opened
  sell_through_pct  — % of venue sold

These replace the estimated velocity in velocity_tracker.py and unlock
the full 20-point TicketManager bonus in heuristic_scorer.py.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass
class TicketManagerData:
    tm_event_id:      str
    sold_in_24h:      Optional[int]   = None
    remaining:        Optional[int]   = None
    capacity:         Optional[int]   = None
    on_sale_date:     Optional[str]   = None
    sell_through_pct: Optional[float] = None
    velocity_tier:    str             = "UNKNOWN"  # FAST / MODERATE / SLOW / UNKNOWN
    source:           str             = "ticketmanager"
    raw:              dict            = field(default_factory=dict)

    def to_heuristic_input(self) -> dict:
        """Returns the dict expected by heuristic_scorer.py's _tm_data block."""
        return {
            "sell_through_pct": self.sell_through_pct,
            "sold_in_24h":      self.sold_in_24h,
            "remaining":        self.remaining,
            "velocity_tier":    self.velocity_tier,
        }

    def to_claude_context(self) -> str:
        """Returns a human-readable string for the Claude analysis prompt."""
        lines = ["TicketManager Sales Data:"]
        if self.sell_through_pct is not None:
            lines.append(f"  Sell-through: {self.sell_through_pct:.1f}%")
        if self.sold_in_24h is not None:
            lines.append(f"  Sold in last 24h: {self.sold_in_24h:,} tickets")
        if self.remaining is not None:
            lines.append(f"  Remaining: {self.remaining:,} tickets")
        if self.velocity_tier != "UNKNOWN":
            lines.append(f"  Velocity: {self.velocity_tier}")
        if self.on_sale_date:
            lines.append(f"  On sale since: {self.on_sale_date}")
        return "\n".join(lines)


class TicketManagerClient:
    """
    TicketManager API client.
    Returns None gracefully if not configured.
    The system falls back to velocity_tracker.py estimates automatically.
    """

    def __init__(self):
        load_dotenv(override=True)
        self.api_key  = os.getenv("TM_MANAGER_API_KEY", "")
        self.base_url = os.getenv("TM_MANAGER_BASE_URL", "").rstrip("/")
        self.enabled  = os.getenv("TM_MANAGER_ENABLED", "false").lower() == "true"

        if self.enabled and not self.api_key:
            logger.warning(
                "TM_MANAGER_ENABLED=true but TM_MANAGER_API_KEY is not set — disabling."
            )
            self.enabled = False

        if self.enabled and not self.base_url:
            logger.warning(
                "TM_MANAGER_ENABLED=true but TM_MANAGER_BASE_URL is not set — disabling."
            )
            self.enabled = False

    def is_ready(self) -> bool:
        return self.enabled and bool(self.api_key) and bool(self.base_url)

    def get_event(self, tm_event_id: str) -> Optional[TicketManagerData]:
        """
        Fetch live sales data for one Ticketmaster event.
        Returns None if not configured or if the request fails.
        """
        if not self.is_ready():
            return None

        try:
            resp = requests.get(
                f"{self.base_url}/events/{tm_event_id}",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept":        "application/json",
                },
                timeout=8,
            )

            if resp.status_code == 404:
                logger.debug(f"TicketManager: event {tm_event_id} not found")
                return None

            if resp.status_code != 200:
                logger.warning(
                    f"TicketManager returned {resp.status_code} for {tm_event_id}"
                )
                return None

            return self._parse(tm_event_id, resp.json())

        except requests.RequestException as e:
            logger.error(f"TicketManager request failed for {tm_event_id}: {e}")
            return None

    def get_batch(self, tm_event_ids: list) -> dict:
        """
        Fetch data for a list of TM event IDs.
        Returns {tm_event_id: TicketManagerData | None}.
        """
        if not self.is_ready():
            return {eid: None for eid in tm_event_ids}

        results = {}
        for eid in tm_event_ids:
            results[eid] = self.get_event(eid)
        return results

    def _parse(self, event_id: str, data: dict) -> TicketManagerData:
        """
        Parse API response into TicketManagerData.

        Field names below are best-guess from the CONTEXT.md description.
        Adjust if the actual API uses different names — check the API docs
        when you receive access.
        """
        # Try multiple possible field name conventions
        sold_24h  = (
            data.get("sold_in_24h") or
            data.get("soldIn24Hours") or
            data.get("sold24h")
        )
        remaining = (
            data.get("remaining") or
            data.get("remainingTickets") or
            data.get("tickets_remaining")
        )
        capacity  = (
            data.get("capacity") or
            data.get("venueCapacity") or
            data.get("venue_capacity")
        )
        on_sale   = (
            data.get("on_sale_date") or
            data.get("onSaleDate") or
            data.get("sale_start_date") or
            ""
        )

        # Calculate sell-through
        sell_pct = None
        if capacity and capacity > 0 and remaining is not None:
            sold     = capacity - remaining
            sell_pct = round(sold / capacity * 100, 1)

        # Classify velocity tier
        tier = "UNKNOWN"
        if sell_pct is not None or sold_24h is not None:
            if (sell_pct and sell_pct >= 80) or (capacity and sold_24h and sold_24h > capacity * 0.05):
                tier = "FAST"
            elif sell_pct and sell_pct >= 50:
                tier = "MODERATE"
            elif sell_pct is not None:
                tier = "SLOW"

        return TicketManagerData(
            tm_event_id      = event_id,
            sold_in_24h      = sold_24h,
            remaining        = remaining,
            capacity         = capacity,
            on_sale_date     = on_sale,
            sell_through_pct = sell_pct,
            velocity_tier    = tier,
            raw              = data,
        )


# ── Module-level singleton ─────────────────────────────────────────────────────

_client: Optional[TicketManagerClient] = None


def get_client() -> TicketManagerClient:
    global _client
    if _client is None:
        _client = TicketManagerClient()
    return _client


def get_event_data(tm_event_id: str) -> Optional[TicketManagerData]:
    """Convenience function — returns None if TicketManager not configured."""
    return get_client().get_event(tm_event_id)


def is_enabled() -> bool:
    """Returns True if TicketManager API is configured and ready."""
    return get_client().is_ready()
