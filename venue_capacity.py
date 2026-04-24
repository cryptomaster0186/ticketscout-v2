"""
venue_capacity.py — Lookup table for venue capacities across UK and EU.

The Ticketmaster Discovery API does NOT return real venue capacity — the
upcomingEvents._total field is the count of scheduled events, not seats.

This module provides:
  - A static lookup dict of 200+ common venues with known capacities
  - fuzzy_capacity(venue_name, city) — best-effort capacity lookup
  - estimate_face_value(capacity, category) — price estimate when TM API returns nothing

Sources: venue websites, Wikipedia, official venue pages.
"""

from difflib import SequenceMatcher
from typing import Optional

# ── Venue capacity table ──────────────────────────────────────────────────────
# Format: "Venue Name Lowercase" -> capacity (standing/seated, whichever is larger)
# Where a venue has multiple configurations, use the standard live music capacity.

VENUE_CAPACITY: dict[str, int] = {

    # ── London ────────────────────────────────────────────────────────────────
    "o2 arena":                          20000,
    "the o2":                            20000,
    "wembley stadium":                   90000,
    "wembley arena":                     12500,
    "ovo arena wembley":                 12500,
    "sse arena wembley":                 12500,
    "royal albert hall":                  5272,
    "o2 academy brixton":                 4921,
    "brixton academy":                    4921,
    "o2 brixton":                         4921,
    "eventim apollo":                     5000,
    "hammersmith apollo":                 5000,
    "alexandra palace":                  10400,
    "ally pally":                        10400,
    "roundhouse":                         3300,
    "electric ballroom":                  1500,
    "scala":                              1100,
    "koko":                               1500,
    "electric brixton":                   1500,
    "o2 forum kentish town":              2300,
    "forum london":                       2300,
    "kentish town forum":                 2300,
    "shepherd's bush empire":             2000,
    "shepherds bush empire":              2000,
    "islington assembly hall":             800,
    "jazz cafe":                           440,
    "100 club":                            350,
    "barbican":                           2026,
    "southbank centre":                   2900,
    "royal festival hall":                2900,
    "heaven":                             1000,
    "fabric":                             1500,
    "xoyo":                               800,
    "village underground":                700,
    "oslo hackney":                       500,
    "moth club":                          300,
    "omeara":                             400,
    "jazz cafe":                          440,
    "100 club":                           350,
    "flat iron square":                   800,
    "o2 shepherd's bush empire":          2000,
    "crystal palace bowl":               30000,
    "finsbury park":                     65000,
    "hyde park":                         65000,
    "bst hyde park":                     65000,
    "victoria park":                     50000,
    "tobacco dock":                       1000,
    "printworks london":                  5000,
    "egg london":                          800,
    "the garage":                          600,
    "the lexington":                       250,
    "water rats":                          200,
    "bull & gate":                         200,

    # ── Manchester ────────────────────────────────────────────────────────────
    "co-op live":                         23500,
    "coop live":                          23500,
    "manchester arena":                   21000,
    "aо arena":                           21000,
    "ao arena":                           21000,
    "aviva studios":                      7000,
    "o2 victoria warehouse":              3000,
    "victoria warehouse":                 3000,
    "manchester apollo":                  3500,
    "o2 apollo manchester":               3500,
    "bridgewater hall":                   2341,
    "albert hall manchester":             1800,
    "the albert hall manchester":         1800,
    "o2 ritz manchester":                 1650,
    "ritz manchester":                    1650,
    "manchester o2 ritz":                 1650,
    "o2 academy manchester":              1000,
    "band on the wall":                    500,
    "gorilla":                             500,
    "yes manchester":                      350,
    "deaf institute":                      450,
    "night people":                        400,
    "the castle hotel":                    200,

    # ── Birmingham ────────────────────────────────────────────────────────────
    "utilita arena birmingham":           15800,
    "resorts world arena":                15800,
    "nec":                                15685,
    "national exhibition centre":        15685,
    "o2 academy birmingham":              3100,
    "o2 institute birmingham":            1400,
    "town hall birmingham":               1500,
    "symphony hall birmingham":           2261,
    "birmingham hippodrome":              1900,

    # ── Glasgow ───────────────────────────────────────────────────────────────
    "ovo hydro":                          13000,
    "sse hydro":                          13000,
    "the hydro":                          13000,
    "glasgow barrowland":                  1900,
    "barrowland ballroom":                 1900,
    "the barrowlands":                     1900,
    "o2 academy glasgow":                  2500,
    "swg3":                                800,
    "king tut's wah wah hut":             300,
    "king tuts":                           300,
    "the garage glasgow":                  500,
    "stereo glasgow":                      350,
    "st luke's glasgow":                  1000,
    "tramway":                             500,
    "celtic park":                        60000,
    "ibrox stadium":                      50000,
    "hampden park":                       52000,

    # ── Edinburgh ────────────────────────────────────────────────────────────
    "edinburgh usher hall":               2200,
    "usher hall":                         2200,
    "edinburgh corn exchange":            3000,
    "corn exchange edinburgh":            3000,
    "o2 academy edinburgh":               1500,
    "edinburgh playhouse":                3059,
    "assembly rooms edinburgh":            800,
    "wee red bar":                         200,

    # ── Leeds ─────────────────────────────────────────────────────────────────
    "first direct arena":                13500,
    "leeds first direct arena":          13500,
    "o2 academy leeds":                   2300,
    "leeds beckett students union":       2000,
    "brudenell social club":               500,
    "belgrave music hall":                 600,
    "hyde park book club":                 350,
    "cathedral":                           200,

    # ── Liverpool ─────────────────────────────────────────────────────────────
    "m&s bank arena":                    11000,
    "echo arena liverpool":              11000,
    "liverpool m&s bank arena":          11000,
    "o2 academy liverpool":               1200,
    "mountford hall":                     2400,
    "guild of students liverpool":        2400,
    "liverpool philharmonic hall":        1750,
    "arts club liverpool":                 700,
    "studio 2":                            200,

    # ── Bristol ───────────────────────────────────────────────────────────────
    "o2 academy bristol":                 1800,
    "bristol beacon":                     2000,
    "marble factory":                     1500,
    "motion bristol":                     2500,
    "trinty centre bristol":               800,
    "thekla":                              400,
    "fleece":                              500,
    "louisiana":                           200,

    # ── Cardiff ───────────────────────────────────────────────────────────────
    "utilita arena cardiff":              7500,
    "cardiff international arena":        7500,
    "motorpoint arena cardiff":           7500,
    "st david's hall cardiff":            2000,
    "cardiff castle":                    15000,
    "millennium stadium":                74500,
    "principality stadium":              74500,

    # ── Newcastle ─────────────────────────────────────────────────────────────
    "utilita arena newcastle":           11000,
    "newcastle utilita arena":           11000,
    "metro radio arena":                 11000,
    "o2 academy newcastle":               2000,
    "newcastle city hall":                2100,
    "boiler shop newcastle":               900,
    "think tank":                          400,

    # ── Sheffield ────────────────────────────────────────────────────────────
    "utilita arena sheffield":           13600,
    "sheffield arena":                   13600,
    "fly dsa arena":                     13600,
    "o2 academy sheffield":               2250,
    "sheffield leadmill":                  900,
    "leadmill":                            900,
    "foundry sheffield":                   500,
    "record junkee":                       350,

    # ── Nottingham ────────────────────────────────────────────────────────────
    "motorpoint arena nottingham":       10000,
    "nottingham arena":                  10000,
    "rock city":                          2000,
    "rescue rooms":                        750,
    "bodega":                              400,

    # ── Brighton ─────────────────────────────────────────────────────────────
    "brighton centre":                    4500,
    "concorde 2":                          600,
    "brighthelm":                          400,
    "brighton dome":                      1700,
    "corn exchange brighton":             2100,

    # ── Southampton / Portsmouth ──────────────────────────────────────────────
    "guildhall southampton":              2000,
    "joiners southampton":                 200,
    "wedgewood rooms":                     500,

    # ── Other UK ─────────────────────────────────────────────────────────────
    "connexin live hull":                  8000,
    "hull arena":                          8000,
    "arena hull":                          8000,
    "o2 academy oxford":                  1100,
    "o2 academy bournemouth":             1600,
    "bournemouth international centre":   4000,
    "bic bournemouth":                    4000,
    "portsmouth guildhall":               2000,
    "cambridge junction":                  800,
    "junction cambridge":                  800,
    "york barbican":                       1900,
    "harrogate convention centre":        2000,
    "royal spa centre":                    800,
    "g-live guildford":                   1700,
    "wylam brewery":                      1000,
    "mill exeter":                         500,
    "exeter great hall":                   700,

    # ── UK Stadiums (used for concerts) ──────────────────────────────────────
    "wembley stadium":                   90000,
    "murrayfield":                       67000,
    "scottish gas murrayfield":          67000,
    "bts murrayfield":                   67000,
    "anfield":                           53000,
    "old trafford":                      74000,
    "emirates stadium":                  60000,
    "tottenham hotspur stadium":         62000,
    "villa park":                        42000,
    "st james park":                     52000,
    "st james' park":                    52000,
    "elland road":                       37000,
    "hampden park":                      52000,
    "barclays hampden":                  52000,
    "celtic park":                       60000,
    "ibrox":                             50000,
    "ibrox stadium":                     50000,
    "principality stadium":              74500,
    "cardiff city stadium":              33000,
    "ashton gate":                       27000,
    "amex stadium":                      31000,
    "london stadium":                    60000,
    "co-op live manchester":             23500,

    # ── Other UK mid-size ─────────────────────────────────────────────────────
    "bridlington spa":                    3500,
    "bridlington spa centre":             3500,
    "o2 apollo":                          3500,
    "civic hall wolverhampton":           3000,
    "wolverhampton civic":                3000,
    "colston hall":                       1900,
    "bristol beacon":                     2000,
    "lemon grove exeter":                  900,
    "engine shed lincoln":                 900,
    "waterfront norwich":                  800,
    "the waterfront":                      800,
    "UEA norwich":                        1650,
    "o2 academy sheffield":               2250,
    "plug sheffield":                      500,
    "picture house edinburgh":            1050,

    # ── UK Festivals / Outdoor ────────────────────────────────────────────────
    "glastonbury festival":             200000,
    "reading festival":                  90000,
    "leeds festival":                    75000,
    "download festival":                 80000,
    "latitude festival":                 35000,
    "wireless festival":                 50000,
    "bst hyde park":                     65000,
    "creamfields":                        70000,
    "bestival":                           35000,
    "isle of wight festival":             60000,

    # ── Germany ───────────────────────────────────────────────────────────────
    "uber arena berlin":                 17000,
    "mercedes-benz arena berlin":        17000,
    "mercedes benz arena":               17000,
    "velodrom berlin":                   11000,
    "columbiahalle":                      3500,
    "tempodrom":                          3500,
    "so36":                               1500,
    "huxleys neue welt":                  1800,
    "kesselhaus berlin":                  1500,
    "arena berlin":                       7500,
    "lido berlin":                         750,
    "cassiopeia":                          600,
    "barclays arena hamburg":            16000,
    "barclaycard arena hamburg":         16000,
    "elbphilharmonie":                    2100,
    "docks hamburg":                      1500,
    "markthalle hamburg":                 1000,
    "grünspan hamburg":                    800,
    "lanxess arena":                     20000,
    "lanxess arena cologne":             20000,
    "e-werk cologne":                     1500,
    "palladium cologne":                  5000,
    "carlswerk victoria cologne":          800,
    "olympiahalle munich":               12000,
    "olympiastadion munich":             69000,
    "zenith munich":                      7000,
    "backstage munich":                   1500,
    "muffathalle":                        1500,
    "strom munich":                        650,
    "festhalle frankfurt":               12000,
    "jahrhunderthalle frankfurt":         6000,
    "batschkapp":                         1000,
    "zoom frankfurt":                      800,
    "commerzbank arena":                 51500,
    "deutsche bank park":                51500,
    "velodrome berlin":                  11000,
    "mitsubishi electric halle":          6000,
    "sse arena berlin":                  17000,

    # ── Netherlands ───────────────────────────────────────────────────────────
    "ziggo dome":                        17000,
    "ziggo dome amsterdam":              17000,
    "paradiso":                           1500,
    "paradiso amsterdam":                 1500,
    "melkweg":                            1500,
    "melkweg amsterdam":                  1500,
    "heineken music hall":                5500,
    "afas live":                          6000,
    "013 tilburg":                        5000,
    "effenaar":                           1000,
    "tivolivredenburg":                   5000,
    "tivoli vredenburg":                  5000,
    "de helling":                          600,
    "vera groningen":                      700,
    "gigant apeldoorn":                    900,

    # ── Belgium ───────────────────────────────────────────────────────────────
    "forest national":                    8000,
    "palais 12":                         12000,
    "lotto arena antwerp":               12000,
    "de roma antwerp":                    1000,
    "ancienne belgique":                  2000,
    "ab brussels":                        2000,
    "botanique brussels":                 1200,
    "sports palace antwerp":             22000,

    # ── France ────────────────────────────────────────────────────────────────
    "stade de france":                   81000,
    "accorhotels arena":                 20000,
    "accor arena":                       20000,
    "bercy":                             20000,
    "zenith paris":                       6300,
    "olympia paris":                      2000,
    "olympia":                            2000,
    "bataclan":                           1500,
    "la cigale":                          1500,
    "elysee montmartre":                  1200,
    "la belle electrique":                 750,
    "transbordeur lyon":                  2000,
    "ninkasi kao":                        1000,

    # ── Spain ─────────────────────────────────────────────────────────────────
    "palau sant jordi":                  16000,
    "wizink center":                     17000,
    "palacio de los deportes":           17000,
    "barclaycard center":                17000,
    "la riviera madrid":                  2000,
    "sala apolo":                         1200,
    "razzmatazz":                         4500,
    "fnac live":                           700,

    # ── Italy ─────────────────────────────────────────────────────────────────
    "unipol forum":                      12700,
    "mediolanum forum":                  12700,
    "pala alpitour":                     12600,
    "palapartenope":                      6000,
    "atlantico live":                     3000,
    "circolo degli artisti":               900,

    # ── Austria ───────────────────────────────────────────────────────────────
    "wien stadthalle":                   16000,
    "wiener stadthalle":                 16000,
    "gasometer wien":                     4000,
    "flex wien":                           800,
    "szene wien":                         1000,

    # ── Switzerland ───────────────────────────────────────────────────────────
    "hallenstadion":                     15000,
    "palexpo":                           12000,
    "samsung hall":                       8000,
    "komplex 457":                         800,
    "exil":                                600,

    # ── Poland ────────────────────────────────────────────────────────────────
    "tauron arena krakow":               17000,
    "atlas arena":                       14000,
    "pge narodowy":                      58000,
    "sts bank arena":                    22000,
    "palladium warsaw":                   2500,
    "progresja":                          2000,
}


# ── Fuzzy lookup ──────────────────────────────────────────────────────────────

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()



# Common sponsor/naming-rights prefixes that get added to venue names
_SPONSOR_PREFIXES = [
    "scottish gas", "barclays", "utilita", "o2", "sse", "ovo", "bts",
    "ao", "first direct", "co-op", "coop", "m&s bank", "resorts world",
    "motorpoint", "samsung", "mercedes-benz", "mercedes benz", "uber",
    "barclaycard", "aviva", "fly dsa", "lotto", "unipol", "mediolanum",
    "pge", "sts bank", "tauron", "connexin", "toyota", "axa",
]


def _strip_sponsor(name: str) -> str:
    """Remove leading sponsor prefix from a venue name."""
    for prefix in _SPONSOR_PREFIXES:
        if name.startswith(prefix + " "):
            return name[len(prefix) + 1:].strip()
    return name


def fuzzy_capacity(venue_name: str, city: str = "") -> Optional[int]:
    """
    Look up venue capacity by name with fuzzy matching.

    Steps:
    1. Exact match (case-insensitive)
    2. Try again after stripping sponsor prefix
    3. Substring match (venue name contains a key or vice-versa)
    4. Fuzzy match with SequenceMatcher (threshold 0.82)

    Returns None if no confident match found.
    """
    if not venue_name:
        return None

    query = venue_name.lower().strip()

    # 1. Exact match
    if query in VENUE_CAPACITY:
        return VENUE_CAPACITY[query]

    # 2. Strip sponsor prefix and retry exact + substring
    stripped = _strip_sponsor(query)
    if stripped != query:
        if stripped in VENUE_CAPACITY:
            return VENUE_CAPACITY[stripped]

    # 3. Substring match — check if any known key is contained in the query
    #    or the query is contained in a known key
    #    Try both original and stripped query
    for q in ([query, stripped] if stripped != query else [query]):
        best_sub: Optional[int] = None
        best_sub_len = 0
        for key, cap in VENUE_CAPACITY.items():
            if key in q or q in key:
                if len(key) > best_sub_len:
                    best_sub = cap
                    best_sub_len = len(key)
        if best_sub is not None:
            return best_sub

    # 4. Fuzzy match (try both original and stripped)
    best_score = 0.0
    best_cap: Optional[int] = None
    for q in ([query, stripped] if stripped != query else [query]):
        for key, cap in VENUE_CAPACITY.items():
            sim = _similarity(q, key)
            if sim > best_score:
                best_score = sim
                best_cap = cap

    if best_score >= 0.82:
        return best_cap

    return None  # No confident match


# ── Face value estimator ──────────────────────────────────────────────────────

def estimate_face_value(capacity: Optional[int], category: str = "music") -> Optional[float]:
    """
    Estimate face value price when TM API returns no price data (which is most UK/EU events).

    Based on typical UK market pricing:
      - Tiny venue (<500):   £15–30   → use £22
      - Small (500–1500):    £20–45   → use £30
      - Mid (1500–5000):     £30–65   → use £45
      - Large (5000–12000):  £50–100  → use £70
      - Arena (12000+):      £70–150  → use £90

    Returns None if capacity is unknown (can't estimate).
    """
    if not capacity or capacity <= 0:
        return None

    if capacity <= 500:
        return 22.0
    elif capacity <= 1500:
        return 30.0
    elif capacity <= 3500:
        return 42.0
    elif capacity <= 6000:
        return 55.0
    elif capacity <= 12000:
        return 70.0
    else:
        return 90.0
