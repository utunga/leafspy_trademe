"""Extract structured fields from cached XHR / __NEXT_DATA__ blobs.

WARNING: TradeMe response shapes are not confirmed at the time of writing.
Field names here are speculative — verify against an actual capture (see
cache/listings/{id}.json after first scrape) and update the *_KEYS lists below.

The cache stores raw_xhr_response intact so this module can be refined
without re-scraping.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from . import classify


# ---------------------------------------------------------------------------
# Field-name candidates — try in order, return first non-null match.
# ---------------------------------------------------------------------------
# TODO(verify): confirm actual key names after first real capture.

_KEY_TITLE = ["Title", "title"]
_KEY_BODY = ["Body", "body", "BodyHtml", "Description", "description"]
_KEY_LISTING_ID = ["ListingId", "listingId", "Id"]
_KEY_YEAR = ["Year", "year"]
_KEY_ODOMETER = ["Odometer", "odometer", "Kilometres"]
_KEY_REGION = ["Region", "region", "RegionName"]
_KEY_SUBURB = ["Suburb", "suburb", "District", "DistrictName"]
_KEY_SELLER = ["IsDealer", "isDealer", "MemberProfile"]
_KEY_BUY_NOW = ["BuyNowPrice", "buyNowPrice"]
_KEY_START_PRICE = ["StartPrice", "startPrice", "ReserveState"]
_KEY_CURRENT_BID = ["CurrentBid", "currentBid", "MaxBidAmount"]
_KEY_CLASSIFIEDS_PRICE = ["AskingPrice", "PriceDisplay", "asking_price"]
_KEY_NEGOTIABLE = ["PriceByNegotiation", "PriceDisplay"]
_KEY_BATTERY_HEALTH = ["BatteryHealth", "batteryHealth", "BatteryCondition"]


def _first_key(d: dict, keys: list[str]) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"\d[\d,]*", v.replace(" ", ""))
        if m:
            return int(m.group(0).replace(",", ""))
    return None


def _to_float(v: Any) -> Optional[float]:
    i = _to_int(v)
    return float(i) if i is not None else None


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "yes", "1")
    return bool(v)


def _strip_html(text: str) -> str:
    """Very light HTML strip — preserves line structure."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(p|div|li)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Main entry: convert a raw listing dict into the schema row
# ---------------------------------------------------------------------------

def parse_listing(raw: dict, listing_url: str) -> dict:
    """Convert a raw TradeMe listing dict into a flat row dict matching schema.CSV_COLUMNS.

    `raw` is whatever was captured from the XHR (or extracted from __NEXT_DATA__).
    """
    title = _first_key(raw, _KEY_TITLE) or ""
    body_raw = _first_key(raw, _KEY_BODY) or ""
    description = _strip_html(body_raw) if "<" in body_raw else body_raw

    year = _to_int(_first_key(raw, _KEY_YEAR))
    odometer = _to_int(_first_key(raw, _KEY_ODOMETER))
    region = _first_key(raw, _KEY_REGION)
    suburb = _first_key(raw, _KEY_SUBURB)

    listing_id = _first_key(raw, _KEY_LISTING_ID)
    if listing_id is None:
        # Derive from URL if missing in payload.
        m = re.search(r"/listing/(\d+)", listing_url)
        listing_id = int(m.group(1)) if m else None

    seller_raw = _first_key(raw, _KEY_SELLER)
    if isinstance(seller_raw, bool):
        seller_type = "dealer" if seller_raw else "private"
    elif isinstance(seller_raw, dict):
        seller_type = "dealer" if seller_raw.get("IsDealer") or seller_raw.get("DealerId") else "private"
    else:
        seller_type = "unknown"

    buy_now_price = _to_float(_first_key(raw, _KEY_BUY_NOW))
    start_price = _to_float(_first_key(raw, _KEY_START_PRICE))
    current_bid = _to_float(_first_key(raw, _KEY_CURRENT_BID))

    classifieds_raw = _first_key(raw, _KEY_CLASSIFIEDS_PRICE)
    # If TradeMe returns "Price by negotiation" as the display string, set is_negotiable.
    is_negotiable = False
    classifieds_price = None
    if isinstance(classifieds_raw, str):
        if re.search(r"negotiation|enquire|make an offer|poa", classifieds_raw, re.IGNORECASE):
            is_negotiable = True
        else:
            classifieds_price = _to_float(classifieds_raw)
    else:
        classifieds_price = _to_float(classifieds_raw)

    # Listing type heuristic
    if buy_now_price and not start_price:
        listing_type = "BuyNow"
    elif start_price:
        listing_type = "auction"
    elif classifieds_price or is_negotiable:
        listing_type = "classified"
    else:
        listing_type = "unknown"

    # Derived price
    effective_price, price_type, price_confidence = classify.derive_effective_price(
        buy_now_price, start_price, current_bid, classifieds_price, is_negotiable
    )

    # Variant
    battery_kwh, battery_kwh_source = classify.parse_battery_kwh(title, description, year)
    trim_grade = classify.parse_trim_grade(title, description)

    # Model variant raw — first non-trivial token after "Leaf"
    m = re.search(r"leaf\s+([^\s,]+(?:\s+\d{2}kwh)?)", title, re.IGNORECASE)
    model_variant_raw = m.group(1) if m else None

    # SOH
    structured_health = _to_float(_first_key(raw, _KEY_BATTERY_HEALTH))
    soh_percent, soh_bars, soh_source = classify.parse_soh(title, description, structured_health)

    # Condition
    battery_status = classify.parse_battery_status(title, description, soh_percent, soh_bars)
    body_status = classify.parse_body_status(title, description)
    damage_type = classify.parse_damage_type(title, description, body_status, battery_status)
    parts_already_stripped = classify.parse_parts_already_stripped(title, description)

    return {
        "listing_id": listing_id,
        "listing_url": listing_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "description": description,
        "year": year,
        "odometer": odometer,
        "region": region,
        "location_suburb": suburb,
        "seller_type": seller_type,
        "listing_type": listing_type,
        "model_variant_raw": model_variant_raw,
        "buy_now_price": buy_now_price,
        "start_price": start_price,
        "current_bid": current_bid,
        "classifieds_price": classifieds_price,
        "is_negotiable": is_negotiable,
        "effective_price": effective_price,
        "price_type": price_type,
        "price_confidence": price_confidence,
        "battery_kwh": battery_kwh,
        "battery_kwh_source": battery_kwh_source,
        "trim_grade": trim_grade,
        "battery_status": battery_status,
        "body_status": body_status,
        "damage_type": damage_type,
        "parts_already_stripped": parts_already_stripped,
        "soh_percent": soh_percent,
        "soh_bars": soh_bars,
        "soh_source": soh_source,
    }


# ---------------------------------------------------------------------------
# __NEXT_DATA__ fallback extraction
# ---------------------------------------------------------------------------

def extract_next_data(html: str) -> Optional[dict]:
    """Pull and parse the __NEXT_DATA__ JSON blob from a TradeMe page's HTML.

    Returns the decoded dict or None if not present.
    """
    import json
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def find_listing_in_next_data(next_data: dict) -> Optional[dict]:
    """Walk a __NEXT_DATA__ tree looking for a listing object.

    Heuristic: a listing-shaped dict has both a Title and either a ListingId
    or a price-shaped key. TradeMe's exact tree shape is unknown — this
    permissive walk is the cost of speculation.
    """
    def walk(node: Any):
        if isinstance(node, dict):
            has_title = any(k in node for k in _KEY_TITLE)
            has_id = any(k in node for k in _KEY_LISTING_ID)
            has_price = any(k in node for k in _KEY_BUY_NOW + _KEY_START_PRICE + _KEY_CLASSIFIEDS_PRICE)
            if has_title and (has_id or has_price):
                return node
            for v in node.values():
                found = walk(v)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found is not None:
                    return found
        return None

    return walk(next_data)
