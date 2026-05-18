"""CSV schema, column dtypes, and shared constants."""
from __future__ import annotations

TRIM_GRADES = ("S", "X", "G", "SV", "SL", "e+", "unknown")
BATTERY_KWH = (24, 30, 40, 62)  # plus None for unknown
BATTERY_STATUSES = ("working", "degraded", "failed", "unknown")
BODY_STATUSES = ("roadworthy", "cosmetic_damage", "structural_damage", "written_off", "unknown")
DAMAGE_TYPES = ("crash", "flood", "fire", "battery_failure", "mechanical", "cosmetic", "none", "unknown")
PRICE_TYPES = ("buy_now", "classifieds", "current_bid", "start_price", "negotiable")
PRICE_CONFIDENCE = ("asking", "auction_active", "auction_unstarted", "negotiable")
SOH_SOURCES = ("structured", "regex_percent", "regex_bars", "none")

# Year → most-likely battery kWh for inference when title doesn't state it.
YEAR_TO_BATTERY_KWH = {
    range(2010, 2013): 24,   # ZE0
    range(2013, 2016): 24,   # AZE0 24kWh
    range(2016, 2018): 30,   # AZE0 30kWh
    range(2018, 2025): 40,   # ZE1 40kWh (and 62 e+ separately handled by title regex)
}

CSV_COLUMNS = [
    # Identity
    "listing_id",
    "listing_url",
    "fetched_at",
    # Raw fields
    "title",
    "description",
    "year",
    "odometer",
    "region",
    "location_suburb",
    "seller_type",
    "listing_type",
    "model_variant_raw",
    # Prices (raw)
    "buy_now_price",
    "start_price",
    "current_bid",
    "classifieds_price",
    "is_negotiable",
    # Prices (derived)
    "effective_price",
    "price_type",
    "price_confidence",
    # Variant (derived)
    "battery_kwh",
    "battery_kwh_source",
    "trim_grade",
    # Condition (derived)
    "battery_status",
    "body_status",
    "damage_type",
    "parts_already_stripped",
    # SOH (derived)
    "soh_percent",
    "soh_bars",
    "soh_source",
]

# Default criteria for "closest comparable" filter, used by summarize.py.
DEFAULT_COMPARABLE_CRITERIA = {
    "match_fields": ("trim_grade", "battery_status", "body_status"),
    "year_tolerance": 2,
}
