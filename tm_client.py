"""
tm_client.py — Ticketmaster Discovery API v2 client.

Automatski pronalazi evente za definisane države i kategorije.
API je besplatan: https://developer.ticketmaster.com

Ključni output po eventu:
  - tm_id, artist, city, country, date, venue, capacity
  - face_value_min / face_value_max
  - status: ON_SALE / SOLD_OUT / OFF_SALE
  - tm_url
"""

import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests

import config
from venue_capacity import fuzzy_capacity, estimate_face_value

logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "TicketScout/2.0",
    "Accept": "application/json",
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get(endpoint: str, params: dict, retries: int = 3) -> Optional[dict]:
    """GET wrapper s retry logikom."""
    url = f"{config.TM_BASE_URL}/{endpoint}"
    params["apikey"] = config.TM_API_KEY

    for attempt in range(retries):
        try:
            resp = SESSION.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"TM rate limit — waiting {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"TM API error {resp.status_code}: {resp.text[:200]}")
                return None
        except requests.RequestException as e:
            logger.error(f"TM request failed (attempt {attempt+1}): {e}")
            time.sleep(1)
    return None


def _parse_event(raw: dict) -> Optional[dict]:
    """
    Parsira jedan TM event dict u naš format.
    Vraća None ako event nema dovoljno podataka.
    """
    try:
        # Osnove
        tm_id = raw.get("id")
        name  = raw.get("name", "Unknown")

        # Artist (attractions)
        attractions = raw.get("_embedded", {}).get("attractions", [])
        artist = attractions[0]["name"] if attractions else name

        # Datum
        dates      = raw.get("dates", {})
        start      = dates.get("start", {})
        event_date = start.get("localDate") or start.get("dateTime", "")[:10]
        if not event_date:
            return None

        # Kategorija (parse early — needed for face value estimate)
        classifications = raw.get("classifications", [])
        segment = ""
        if classifications:
            segment = classifications[0].get("segment", {}).get("name", "music").lower()

        # Venue + lokacija
        venues  = raw.get("_embedded", {}).get("venues", [])
        venue   = venues[0].get("name", "") if venues else ""
        city    = venues[0].get("city", {}).get("name", "") if venues else ""
        country = venues[0].get("country", {}).get("countryCode", "") if venues else ""

        # Status — TM uses "onsale"/"offsale", no separate "presale"/"soldout" codes.
        # Presale: check sales.presales[] for an active window (now between start/end).
        # Sold out: TM returns "offsale" — we keep it as OFF_SALE (no reliable sold-out flag).
        status_obj = dates.get("status", {})
        tm_status  = status_obj.get("code", "onsale").upper()
        status_map = {
            "ONSALE":      "ON_SALE",
            "OFFSALE":     "OFF_SALE",
            "CANCELLED":   "CANCELLED",
            "POSTPONED":   "POSTPONED",
            "RESCHEDULED": "RESCHEDULED",
        }
        status = status_map.get(tm_status, "ON_SALE")

        # Override to PRESALE if currently inside an active presale window
        if status == "ON_SALE":
            now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            presales = raw.get("sales", {}).get("presales", [])
            for ps in presales:
                ps_start = ps.get("startDateTime", "")
                ps_end   = ps.get("endDateTime", "")
                if ps_start and ps_end and ps_start <= now_iso <= ps_end:
                    status = "PRESALE"
                    break

        # Kapacitet — TM API ne vraća pravi kapacitet, koristimo lookup tablicu
        capacity = None
        if venues:
            cap = fuzzy_capacity(venues[0].get("name", ""), city)
            if cap:
                capacity = cap

        # Cijene — TM API ne vraća cijene za UK/EU evente u besplatnom tieru
        # Pokušavamo iz API-ja, fallback na estimaciju po kapacitetu
        price_ranges = raw.get("priceRanges", [])
        face_min = face_max = None
        currency = "GBP"
        if price_ranges:
            face_min = price_ranges[0].get("min")
            face_max = price_ranges[0].get("max")
            currency = price_ranges[0].get("currency", "GBP")

        # Fallback: estimate face value from capacity when API returns nothing
        if face_min is None and capacity:
            estimated = estimate_face_value(capacity, segment)
            if estimated:
                face_min = estimated
                face_max = estimated * 1.5  # rough upper tier estimate

        # URL
        tm_url = raw.get("url", "")

        return {
            "tm_id":          tm_id,
            "artist":         artist,
            "city":           city,
            "country":        country,
            "event_date":     event_date,
            "venue":          venue,
            "capacity":       capacity,
            "face_value_min": face_min,
            "face_value_max": face_max,
            "currency":       currency,
            "tm_url":         tm_url,
            "category":       segment,
            "status":         status,
        }
    except Exception as e:
        logger.debug(f"Skipping event parse error: {e}")
        return None


# ── Capacity filter ────────────────────────────────────────────────────────────

def _passes_capacity_filter(event: dict) -> bool:
    """
    Ako je kapacitet poznat, filtriramo po konfiguraciji.
    Ako nije poznat, propuštamo dalje (bolje propustiti nego ignorisati).
    """
    cap = event.get("capacity")
    if cap is None:
        return True
    return config.MIN_VENUE_CAPACITY <= cap <= config.MAX_VENUE_CAPACITY


# ── Main discovery ─────────────────────────────────────────────────────────────

def discover_events(country: str, category: str = "music") -> List[dict]:
    """
    Pronalazi evente za jednu državu i kategoriju.
    Paginira kroz sve rezultate.

    TM API limit: (page * size) must be < 1000, i.e. max 4 pages of 200.
    For countries with >800 events we split into monthly date windows to
    stay within the limit and cover all results.
    """
    start_dt = datetime.utcnow()
    end_dt   = start_dt + timedelta(days=config.TM_MONTHS_AHEAD * 30)

    # Build date windows: split into monthly chunks to avoid paging limit
    windows = []
    cur = start_dt
    while cur < end_dt:
        nxt = min(cur + timedelta(days=32), end_dt)
        windows.append((
            cur.strftime("%Y-%m-%dT%H:%M:%SZ"),
            nxt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ))
        cur = nxt

    all_events: dict = {}  # de-dup by tm_id within this country

    for win_start, win_end in windows:
        page        = 0
        total_pages = 1

        while page < total_pages:
            params = {
                "countryCode":          country,
                "classificationName":   category,
                "startDateTime":        win_start,
                "endDateTime":          win_end,
                "size":                 200,
                "page":                 page,
                "sort":                 "date,asc",
                "includeFamily":        "no",
            }
            data = _get("events.json", params)
            if not data:
                break

            page_info   = data.get("page", {})
            total_pages = min(page_info.get("totalPages", 1), 4)  # API hard cap: page*size < 1000

            raw_events = data.get("_embedded", {}).get("events", [])
            for raw in raw_events:
                parsed = _parse_event(raw)
                if parsed and _passes_capacity_filter(parsed):
                    all_events[parsed["tm_id"]] = parsed

            logger.debug(f"  {country}/{category} [{win_start[:7]}] page {page+1}/{total_pages}: "
                         f"+{len(raw_events)} events")
            page += 1

            if page < total_pages:
                time.sleep(0.3)  # polite rate limiting

        time.sleep(0.2)  # between windows

    events = list(all_events.values())
    return events


def discover_all() -> List[dict]:
    """
    Prolazi kroz sve konfigurisane države i kategorije.
    Vraća de-duplikovanu listu evenata.
    """
    all_events = {}
    total_raw  = 0

    for country in config.TM_COUNTRIES:
        for category in config.TM_CATEGORIES:
            logger.info(f"Discovering: {country} / {category}")
            found = discover_events(country, category)
            total_raw += len(found)
            for e in found:
                all_events[e["tm_id"]] = e  # de-duplikacija po tm_id
            time.sleep(0.5)

    result = list(all_events.values())
    logger.info(f"Discovery complete: {total_raw} raw → {len(result)} unique events")
    return result


def check_event_availability(tm_id: str) -> Optional[dict]:
    """
    Provjerava trenutni status jednog eventa (za restock monitoring).
    Vraća dict s tm_status, face_value_min, face_value_max.
    """
    data = _get("events.json", {"id": tm_id})
    if not data:
        return None

    events = data.get("_embedded", {}).get("events", [])
    if not events:
        return None

    return _parse_event(events[0])
