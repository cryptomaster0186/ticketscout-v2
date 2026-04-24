"""
secondary_market.py — Scraper za sekundarno tržište.

Strategija (u redoslijedu pokušaja):
  1. Viagogo  — primarni EU/UK resale market
  2. StubHub  — fallback za UK
  3. Estimated — ako oba failu, Claude procjenjuje na osnovu signala

Vraća SecondaryData ili None ako nema podataka.
"""

import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

import requests

import config

logger = logging.getLogger(__name__)

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class SecondaryData:
    source: str           # "viagogo" | "stubhub" | "estimated"
    low_ask: float
    median_ask: Optional[float]
    high_ask: Optional[float]
    listings: Optional[int]
    spread_pct: float                    # (low_ask / face_value) - 1
    net_profit_est: float                # low_ask * (1 - FRICTION) - face_value
    confidence: str = "HIGH"            # HIGH | MEDIUM | LOW


# ── Browser headers ────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "DNT": "1",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ── Viagogo ────────────────────────────────────────────────────────────────────

def _parse_viagogo_prices(html: str) -> Optional[dict]:
    """
    Pokušava izvući cijene iz Viagogo HTML-a.
    Viagogo embeduje JSON-LD i script data u stranicu.
    """
    prices = []

    # Strategy 1: JSON-LD structured data
    jsonld_matches = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    )
    for block in jsonld_matches:
        try:
            import json
            data = json.loads(block)
            if isinstance(data, list):
                data = data[0]
            offers = data.get("offers", {})
            if isinstance(offers, dict):
                price = offers.get("lowPrice") or offers.get("price")
                if price:
                    prices.append(float(price))
            elif isinstance(offers, list):
                for offer in offers:
                    p = offer.get("price") or offer.get("lowPrice")
                    if p:
                        prices.append(float(p))
        except Exception:
            continue

    # Strategy 2: Price regex patterns in HTML
    if not prices:
        # Match "£45" or "€52" or "$30" style
        price_matches = re.findall(r'[£€\$][\s]*([\d,]+\.?\d*)', html)
        for m in price_matches[:20]:
            try:
                p = float(m.replace(",", ""))
                if 5 < p < 5000:
                    prices.append(p)
            except ValueError:
                continue

    if not prices:
        return None

    prices.sort()
    return {
        "low": prices[0],
        "median": prices[len(prices) // 2] if len(prices) > 1 else prices[0],
        "high": prices[-1],
        "count": len(prices),
    }


def fetch_viagogo(artist: str, city: str, event_date: str,
                  face_value: float) -> Optional[SecondaryData]:
    """Scrape Viagogo za cijene resalea."""
    query = quote_plus(f"{artist} {city}")
    url   = f"https://www.viagogo.com/Concert-Tickets/search?q={query}"

    try:
        resp = SESSION.get(url, timeout=15)
        if resp.status_code != 200:
            logger.debug(f"Viagogo returned {resp.status_code} for {artist}")
            return None

        parsed = _parse_viagogo_prices(resp.text)
        if not parsed or parsed["low"] <= 0:
            return None

        # Plausibility check — low ask treba biti >= face value
        # (ne mora biti, ali ako je dramatično niže, vjerovatno je parsing error)
        if parsed["low"] < face_value * 0.5:
            logger.debug(f"Viagogo price {parsed['low']} implausibly low vs face {face_value}")
            return None

        spread_pct     = (parsed["low"] / face_value) - 1 if face_value else 0
        net_profit_est = (parsed["low"] * (1 - config.FRICTION_RATE)) - face_value

        return SecondaryData(
            source="viagogo",
            low_ask=parsed["low"],
            median_ask=parsed.get("median"),
            high_ask=parsed.get("high"),
            listings=parsed.get("count"),
            spread_pct=spread_pct,
            net_profit_est=net_profit_est,
            confidence="MEDIUM",  # scraping, ne API
        )

    except requests.RequestException as e:
        logger.debug(f"Viagogo request error: {e}")
        return None


# ── StubHub ────────────────────────────────────────────────────────────────────

def fetch_stubhub(artist: str, city: str, event_date: str,
                  face_value: float) -> Optional[SecondaryData]:
    """
    StubHub search scraper (fallback za Viagogo).
    """
    query = quote_plus(f"{artist} {city}")
    url   = f"https://www.stubhub.com/find/s/?q={query}"

    try:
        resp = SESSION.get(url, timeout=15)
        if resp.status_code != 200:
            return None

        html   = resp.text
        prices = []

        # StubHub embeduje cijene u JSON u stranici
        matches = re.findall(r'"listingPrice"\s*:\s*\{[^}]*"amount"\s*:\s*([\d.]+)', html)
        for m in matches[:15]:
            try:
                p = float(m)
                if 5 < p < 5000:
                    prices.append(p)
            except ValueError:
                continue

        if not prices:
            # Fallback — generic price pattern
            price_matches = re.findall(r'\$\s*([\d,]+\.?\d*)', html)
            for m in price_matches[:20]:
                try:
                    p = float(m.replace(",", ""))
                    if 5 < p < 5000:
                        prices.append(p)
                except ValueError:
                    continue

        if not prices:
            return None

        prices.sort()
        low    = prices[0]
        median = prices[len(prices) // 2]

        if low < face_value * 0.5:
            return None

        spread_pct     = (low / face_value) - 1 if face_value else 0
        net_profit_est = (low * (1 - config.FRICTION_RATE)) - face_value

        return SecondaryData(
            source="stubhub",
            low_ask=low,
            median_ask=median,
            high_ask=prices[-1],
            listings=len(prices),
            spread_pct=spread_pct,
            net_profit_est=net_profit_est,
            confidence="MEDIUM",
        )

    except requests.RequestException as e:
        logger.debug(f"StubHub request error: {e}")
        return None


# ── Estimated (Claude fallback) ────────────────────────────────────────────────

def estimate_secondary(face_value: float, demand_signals: dict) -> SecondaryData:
    """
    Kada scraping ne uspije, procjenjujemo secondary market cijene
    na osnovu demand signala (capacity, sold_pct, velocity).

    Ovo je konzervativna procjena — uvijek označena kao LOW confidence.
    """
    sold_pct = demand_signals.get("sold_pct", 0.5) or 0.5
    capacity = demand_signals.get("capacity") or 0

    # When capacity is unknown, back-calculate tier from face value.
    # Cheap tickets (sub-£30) → small intimate venue.
    # This produces varied estimates instead of a uniform default.
    if capacity <= 0:
        if face_value <= 25:
            capacity = 800       # small club
        elif face_value <= 35:
            capacity = 1_500     # medium club
        elif face_value <= 55:
            capacity = 4_000     # theatre
        elif face_value <= 80:
            capacity = 8_000     # mid-size arena
        else:
            capacity = 15_000    # large arena

    # Manji venue + veća rasprodatost = veći multiplier
    scarcity_mult = 1.0 + (sold_pct * 1.5)
    if capacity <= 500:
        venue_mult = 2.0
    elif capacity <= 2_000:
        venue_mult = 1.7
    elif capacity <= 5_000:
        venue_mult = 1.4
    elif capacity <= 10_000:
        venue_mult = 1.2
    else:
        venue_mult = 1.0

    multiplier     = min(scarcity_mult * venue_mult, 3.5)
    low_ask        = round(face_value * multiplier, 2)
    spread_pct     = multiplier - 1
    net_profit_est = (low_ask * (1 - config.FRICTION_RATE)) - face_value

    return SecondaryData(
        source="estimated",
        low_ask=low_ask,
        median_ask=round(low_ask * 1.2, 2),
        high_ask=round(low_ask * 2.0, 2),
        listings=None,
        spread_pct=spread_pct,
        net_profit_est=net_profit_est,
        confidence="LOW",
    )


# ── Main entry point ───────────────────────────────────────────────────────────

def get_secondary_data(artist: str, city: str, event_date: str,
                       face_value: float,
                       demand_signals: Optional[dict] = None) -> SecondaryData:
    """
    Pokušava Viagogo → StubHub → Estimate.
    Uvijek vraća nešto (nikad None).
    """
    if not face_value or face_value <= 0:
        face_value = 40.0  # default ako nemamo face value

    # Random delay za anti-bot
    time.sleep(random.uniform(1.0, 2.5))

    # 1. Viagogo
    result = fetch_viagogo(artist, city, event_date, face_value)
    if result:
        logger.debug(f"Viagogo data for {artist} {city}: £{result.low_ask}")
        return result

    time.sleep(random.uniform(0.5, 1.5))

    # 2. StubHub
    result = fetch_stubhub(artist, city, event_date, face_value)
    if result:
        logger.debug(f"StubHub data for {artist} {city}: £{result.low_ask}")
        return result

    # 3. Estimated
    logger.debug(f"Using estimated secondary data for {artist} {city}")
    return estimate_secondary(face_value, demand_signals or {})
