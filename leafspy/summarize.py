"""Closest-comparables markdown summary for a seller's car."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd


@dataclass
class SellerCar:
    """Specification of the car being priced."""
    trim_grade: str = "G"
    battery_kwh: Optional[int] = 30
    year: int = 2016
    battery_status: str = "failed"      # this user's case: shot battery
    body_status: str = "roadworthy"     # otherwise OK


def summarize(
    df: pd.DataFrame,
    seller: SellerCar = SellerCar(),
    year_tolerance: int = 2,
) -> str:
    """Return markdown summary of the seller's comparable market position."""
    total = len(df)
    priced = df[df["effective_price"].notna()]

    # ---- Direct comparables ----
    direct = priced[
        (priced["trim_grade"] == seller.trim_grade)
        & (priced["battery_status"] == seller.battery_status)
        & (priced["body_status"] == seller.body_status)
        & priced["year"].between(seller.year - year_tolerance, seller.year + year_tolerance)
    ]

    # ---- Adjacent cells ----
    working_version = priced[
        (priced["trim_grade"] == seller.trim_grade)
        & (priced["battery_status"] == "working")
        & (priced["body_status"] == "roadworthy")
        & priced["year"].between(seller.year - year_tolerance, seller.year + year_tolerance)
    ]
    donor_cars = priced[
        (priced["battery_status"] == "working")
        & (priced["body_status"].isin(["written_off", "structural_damage"]))
    ]

    lines = []
    lines.append(f"# Leaf market summary — generated {datetime.now().strftime('%Y-%m-%d')}\n")
    lines.append(f"Sample: **{total} listings** captured, **{len(priced)} with usable price data**.\n")

    seller_label = (
        f"trim_grade={seller.trim_grade}, battery_status={seller.battery_status}, "
        f"body_status={seller.body_status}, year ≈ {seller.year - year_tolerance}–{seller.year + year_tolerance}"
    )
    lines.append("\n## Your car's direct comparables\n")
    lines.append(f"Filter: `{seller_label}`\n")
    lines.append(f"Matches: **{len(direct)} listings**\n")

    if len(direct) > 0:
        prices = direct["effective_price"]
        lines.append("")
        lines.append(f"  - Median asking:   ${prices.median():,.0f}")
        lines.append(f"  - Range (P25–P75): ${prices.quantile(0.25):,.0f} – ${prices.quantile(0.75):,.0f}")
        lines.append(f"  - Min – Max:       ${prices.min():,.0f} – ${prices.max():,.0f}")

        # Dealer vs private skew
        dealer = direct[direct["seller_type"] == "dealer"]["effective_price"]
        private = direct[direct["seller_type"] == "private"]["effective_price"]
        if len(dealer) > 0 and len(private) > 0:
            pct = (dealer.median() / private.median() - 1) * 100
            lines.append(
                f"  - Skew: dealers ask ${dealer.median():,.0f} (n={len(dealer)}) "
                f"vs private ${private.median():,.0f} (n={len(private)}) → {pct:+.0f}%"
            )
    else:
        lines.append("\n*(No direct matches — consider loosening filter criteria.)*")

    lines.append("\n## Adjacent market segments\n")

    if len(working_version) > 0:
        lines.append(
            f"- **Working version (same trim, healthy battery, roadworthy):** "
            f"median ${working_version['effective_price'].median():,.0f} (n={len(working_version)}) "
            "— what yours would be worth if battery were fine"
        )
    if len(donor_cars) > 0:
        lines.append(
            f"- **Donor cars (working battery, written-off / structural body):** "
            f"median ${donor_cars['effective_price'].median():,.0f} (n={len(donor_cars)}) "
            "— what a buyer would pair with yours"
        )

    if len(working_version) > 0 and len(donor_cars) > 0:
        implied_shell = working_version["effective_price"].median() - donor_cars["effective_price"].median()
        lines.append(
            f"- **Implied shell value:** ${working_version['effective_price'].median():,.0f} − "
            f"${donor_cars['effective_price'].median():,.0f} ≈ **${implied_shell:,.0f}** "
            "— a theoretical upper bound for your ask"
        )

    # ---- Suggested ask range ----
    if len(direct) >= 3:
        lines.append("\n## Suggested ask range\n")
        prices = direct["effective_price"]
        lines.append(f"- Conservative (P25): **${prices.quantile(0.25):,.0f}**")
        lines.append(f"- Realistic   (P50): **${prices.median():,.0f}**")
        if len(working_version) > 0 and len(donor_cars) > 0:
            implied_shell = working_version["effective_price"].median() - donor_cars["effective_price"].median()
            lines.append(f"- Optimistic (implied shell value): **${max(prices.quantile(0.75), implied_shell):,.0f}**")
        else:
            lines.append(f"- Optimistic  (P75): **${prices.quantile(0.75):,.0f}**")

    return "\n".join(lines)


def write_summary_file(df: pd.DataFrame, output_path: str, seller: SellerCar = SellerCar()) -> str:
    """Write summary markdown to a file. Returns the markdown content."""
    md = summarize(df, seller)
    with open(output_path, "w") as f:
        f.write(md)
    return md
