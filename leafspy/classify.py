"""Classification rules for raw listing text.

All functions accept (title, description) strings and return a structured value.
Rules are heuristic and meant to be refined after seeing real data — the per-listing
JSON cache makes that cheap (re-derive CSV without re-scraping).
"""
from __future__ import annotations

import re
from typing import Optional

from .schema import YEAR_TO_BATTERY_KWH


def _norm(*parts: Optional[str]) -> str:
    """Concatenate text parts, normalised for matching."""
    return " ".join(p or "" for p in parts).lower()


# ---------------------------------------------------------------------------
# Battery kWh + source
# ---------------------------------------------------------------------------

_KWH_PATTERN = re.compile(r"(\d{2})\s*kwh", re.IGNORECASE)
_E_PLUS_PATTERN = re.compile(r"\be\s*\+\b|\be-plus\b", re.IGNORECASE)


def parse_battery_kwh(title: str, description: str, year: Optional[int]) -> tuple[Optional[int], str]:
    """Return (kwh, source). Source ∈ {'explicit_in_title', 'inferred_from_year', 'unknown'}."""
    text = _norm(title, description)

    if _E_PLUS_PATTERN.search(title or ""):
        return 62, "explicit_in_title"

    m = _KWH_PATTERN.search(title or "")
    if m:
        return int(m.group(1)), "explicit_in_title"

    m = _KWH_PATTERN.search(description or "")
    if m:
        return int(m.group(1)), "explicit_in_title"

    if year is not None:
        for year_range, kwh in YEAR_TO_BATTERY_KWH.items():
            if year in year_range:
                return kwh, "inferred_from_year"

    return None, "unknown"


# ---------------------------------------------------------------------------
# Trim grade
# ---------------------------------------------------------------------------

# Order matters — match longer/more-specific first.
_TRIM_PATTERNS = [
    ("e+", re.compile(r"\be\s*\+|\be-plus\b", re.IGNORECASE)),
    ("SV", re.compile(r"\bSV\b")),
    ("SL", re.compile(r"\bSL\b")),
    # "30G" / "24G" / "G grade" / " G "
    ("G", re.compile(r"\b\d{2}G\b|\bG\s*grade\b|(?<![A-Za-z])G(?![A-Za-z])")),
    ("X", re.compile(r"\b\d{2}X\b|\bX\s*grade\b|(?<![A-Za-z])X(?![A-Za-z])")),
    ("S", re.compile(r"\b\d{2}S\b|\bS\s*grade\b|(?<![A-Za-z])S(?![A-Za-z])")),
]


def parse_trim_grade(title: str, description: str) -> str:
    """Return trim grade string ('S' / 'X' / 'G' / 'SV' / 'SL' / 'e+' / 'unknown')."""
    # Title is far more reliable than description for trim — try it first only.
    for trim, pattern in _TRIM_PATTERNS:
        if pattern.search(title or ""):
            return trim
    # Fall back to description with the same patterns.
    for trim, pattern in _TRIM_PATTERNS:
        if pattern.search(description or ""):
            return trim
    return "unknown"


# ---------------------------------------------------------------------------
# Battery status
# ---------------------------------------------------------------------------

_BATTERY_FAILED_KEYWORDS = [
    r"battery (?:is )?(?:shot|dead|failed|cooked|stuffed|rooted|munted|kaput)",
    r"(?:dead|no|missing|failed|shot|cooked|stuffed) battery",
    r"battery needs replac",
    r"needs (?:a )?new battery",
    r"battery has failed",
    r"won't (?:charge|hold a charge)",
    r"battery (?:gone|toast|done)",
]
_BATTERY_DEGRADED_KEYWORDS = [
    r"battery (?:is )?(?:weak|tired|poor|low|degraded|aged)",
    r"(?:weak|tired|poor|low|degraded) battery",
    r"low (?:soh|state of health)",
    r"battery health (?:low|poor)",
    r"low range",
    r"limited range",
    r"reduced range",
    r"only (?:gets|does|getting) \d+ ?km",
]
_BATTERY_WORKING_KEYWORDS = [
    r"battery (?:is )?(?:great|good|excellent|strong|healthy|new|replaced)",
    r"(?:great|good|excellent|strong|healthy|new|replaced) battery",
    r"high (?:soh|state of health)",
    r"battery health (?:high|good|excellent)",
    r"long range",
    r"full range",
    r"12 ?bars",
    r"all 12 ?bars",
    r"new battery installed",
    r"battery replacement",
]


def parse_battery_status(title: str, description: str, soh_percent: Optional[float], soh_bars: Optional[int]) -> str:
    """Return 'working' / 'degraded' / 'failed' / 'unknown'.

    SOH signals take priority over keyword heuristics when present.
    """
    if soh_percent is not None:
        if soh_percent < 50:
            return "failed"
        if soh_percent < 75:
            return "degraded"
        return "working"
    if soh_bars is not None:
        if soh_bars <= 6:
            return "failed"
        if soh_bars <= 10:
            return "degraded"
        return "working"

    text = _norm(title, description)
    if any(re.search(p, text) for p in _BATTERY_FAILED_KEYWORDS):
        return "failed"
    if any(re.search(p, text) for p in _BATTERY_DEGRADED_KEYWORDS):
        return "degraded"
    if any(re.search(p, text) for p in _BATTERY_WORKING_KEYWORDS):
        return "working"
    return "unknown"


# ---------------------------------------------------------------------------
# Body status
# ---------------------------------------------------------------------------

_BODY_WRITTEN_OFF_KEYWORDS = [
    r"\bwrite[ -]?off\b",
    r"\bwritten[ -]?off\b",
    r"\bWoF declined\b",  # case-insensitive elsewhere
    r"insurance write",
    r"insurance loss",
    r"statutory write",
    r"\bscrap\b",
    r"for parts only",
    r"parts only",
    r"wreck(?:ed|ing)?",
    r"non[ -]?repairable",
]
_BODY_STRUCTURAL_KEYWORDS = [
    r"rear[ -]ended",
    r"front[ -]ended",
    r"hit (?:in )?the (?:front|rear|side)",
    r"side impact",
    r"chassis damage",
    r"frame damage",
    r"rebuildable",
    r"repairable",
    r"hail damage",  # NZ less common but appears
    r"crash(?:ed)?",
    r"accident damage",
    r"structural",
]
_BODY_COSMETIC_KEYWORDS = [
    r"minor damage",
    r"cosmetic",
    r"scratched?",
    r"dented?",
    r"scuff",
    r"small (?:dent|scratch)",
    r"bumper damage",
    r"paintwork",
    r"tidy little",
]
_BODY_ROADWORTHY_KEYWORDS = [
    r"great condition",
    r"excellent condition",
    r"good condition",
    r"no damage",
    r"immaculate",
    r"tidy",
    r"clean (?:car|leaf)",
    r"drives well",
    r"runs and drives",
    r"current wof",
    r"new wof",
    r"current rego",
]


def parse_body_status(title: str, description: str) -> str:
    """Return 'roadworthy' / 'cosmetic_damage' / 'structural_damage' / 'written_off' / 'unknown'."""
    text = _norm(title, description)
    if any(re.search(p, text, re.IGNORECASE) for p in _BODY_WRITTEN_OFF_KEYWORDS):
        return "written_off"
    if any(re.search(p, text, re.IGNORECASE) for p in _BODY_STRUCTURAL_KEYWORDS):
        return "structural_damage"
    if any(re.search(p, text, re.IGNORECASE) for p in _BODY_COSMETIC_KEYWORDS):
        return "cosmetic_damage"
    if any(re.search(p, text, re.IGNORECASE) for p in _BODY_ROADWORTHY_KEYWORDS):
        return "roadworthy"
    return "unknown"


# ---------------------------------------------------------------------------
# Damage type
# ---------------------------------------------------------------------------

_DAMAGE_TYPE_PATTERNS = [
    ("crash", [r"crash", r"rear[ -]ended", r"front[ -]ended", r"side impact", r"accident", r"collision", r"hit (?:in )?the"]),
    ("flood", [r"flood", r"water damage", r"submerged"]),
    ("fire", [r"\bfire\b", r"burnt", r"burn damage", r"fire damage"]),
    ("battery_failure", [r"battery (?:shot|dead|failed|won't charge)", r"failed battery", r"dead battery"]),
    ("mechanical", [r"motor (?:fault|failed|burnt)", r"inverter (?:fault|failed)", r"transmission", r"won't drive"]),
    ("cosmetic", [r"cosmetic (?:damage|only)", r"minor damage", r"scratched?", r"dented?"]),
]


def parse_damage_type(title: str, description: str, body_status: str, battery_status: str) -> str:
    """Return damage type. If body and battery both look fine, return 'none'."""
    text = _norm(title, description)
    for damage, patterns in _DAMAGE_TYPE_PATTERNS:
        if any(re.search(p, text) for p in patterns):
            return damage
    if body_status == "roadworthy" and battery_status == "working":
        return "none"
    return "unknown"


# ---------------------------------------------------------------------------
# Parts-already-stripped
# ---------------------------------------------------------------------------

_STRIPPED_PATTERNS = [
    r"stripped",
    r"parts removed",
    r"no battery",
    r"battery removed",
    r"missing (?:parts|battery|wheels|seats)",
    r"sold without",
]


def parse_parts_already_stripped(title: str, description: str) -> bool:
    text = _norm(title, description)
    return any(re.search(p, text) for p in _STRIPPED_PATTERNS)


# ---------------------------------------------------------------------------
# SOH (three-source)
# ---------------------------------------------------------------------------

_SOH_PERCENT_PATTERNS = [
    re.compile(r"\bsoh[:\s]+(\d{2,3})\s*%", re.IGNORECASE),
    re.compile(r"(\d{2,3})\s*%\s*soh", re.IGNORECASE),
    re.compile(r"battery health[:\s]+(\d{2,3})\s*%", re.IGNORECASE),
    re.compile(r"state of health[:\s]+(\d{2,3})\s*%", re.IGNORECASE),
    re.compile(r"\bsoh[:\s]+(\d{2,3})\b(?!\s*bar)", re.IGNORECASE),  # "SOH 88" with no % sign
]
_SOH_BARS_PATTERNS = [
    re.compile(r"(\d{1,2})\s*/\s*12\s*bars", re.IGNORECASE),
    re.compile(r"(\d{1,2})\s*bars?\s*(?:showing|remaining|left)", re.IGNORECASE),
    re.compile(r"showing\s*(\d{1,2})\s*bars?", re.IGNORECASE),
    re.compile(r"all\s*(\d{1,2})\s*bars", re.IGNORECASE),
]

# Approximate bars → % mapping for NZ Leaf 24/30kWh chemistry.
_BARS_TO_PERCENT = {12: 100, 11: 85, 10: 78, 9: 70, 8: 63, 7: 56, 6: 49, 5: 42, 4: 35, 3: 28, 2: 21, 1: 14}


def parse_soh(
    title: str,
    description: str,
    structured_health: Optional[float] = None,
) -> tuple[Optional[float], Optional[int], str]:
    """Return (soh_percent, soh_bars, source).

    Source priority:
      1. structured  — value from TradeMe structured field (if present in scrape)
      2. regex_percent — explicit % in title/description
      3. regex_bars  — explicit bars count, mapped to approximate %
      4. none        — nothing found
    """
    if structured_health is not None:
        return float(structured_health), None, "structured"

    text = f"{title or ''}\n{description or ''}"

    for pat in _SOH_PERCENT_PATTERNS:
        m = pat.search(text)
        if m:
            value = int(m.group(1))
            if 30 <= value <= 100:  # sanity-bound
                return float(value), None, "regex_percent"

    for pat in _SOH_BARS_PATTERNS:
        m = pat.search(text)
        if m:
            bars = int(m.group(1))
            if 1 <= bars <= 12:
                approx = _BARS_TO_PERCENT.get(bars)
                return float(approx) if approx else None, bars, "regex_bars"

    return None, None, "none"


# ---------------------------------------------------------------------------
# Effective price + price confidence
# ---------------------------------------------------------------------------

def derive_effective_price(
    buy_now_price: Optional[float],
    start_price: Optional[float],
    current_bid: Optional[float],
    classifieds_price: Optional[float],
    is_negotiable: bool,
) -> tuple[Optional[float], Optional[str], str]:
    """Return (effective_price, price_type, price_confidence)."""
    if buy_now_price is not None:
        return float(buy_now_price), "buy_now", "asking"
    if classifieds_price is not None:
        return float(classifieds_price), "classifieds", "asking"
    if current_bid is not None and start_price is not None and current_bid > start_price:
        return float(current_bid), "current_bid", "auction_active"
    if start_price is not None:
        # Auction with no real bids yet — low signal.
        return float(start_price), "start_price", "auction_unstarted"
    if is_negotiable:
        return None, None, "negotiable"
    return None, None, "negotiable"
