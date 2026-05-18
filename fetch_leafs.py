#!/usr/bin/env python3
"""Leafspy CLI — scrape TradeMe Nissan Leaf listings and build CSV.

See SPEC.md §10 for flag reference.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd

from leafspy.parse import parse_listing
from leafspy.schema import CSV_COLUMNS
from leafspy.scrape import LeafScraper, ScrapeAbort


def setup_logging(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    log_path = cache_dir / "scrape.log"
    err_path = cache_dir / "scrape.log.errors"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setFormatter(fmt)
    stdout_h.setLevel(logging.INFO)
    root.addHandler(stdout_h)

    file_h = logging.FileHandler(log_path, mode="a")
    file_h.setFormatter(fmt)
    file_h.setLevel(logging.DEBUG)
    root.addHandler(file_h)

    err_h = logging.FileHandler(err_path, mode="a")
    err_h.setFormatter(fmt)
    err_h.setLevel(logging.WARNING)
    root.addHandler(err_h)


def build_csv(listings_dir: Path, output_csv: Path) -> int:
    """Read every cache/listings/*.json, parse, write CSV. Returns row count."""
    rows = []
    for path in sorted(listings_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError:
            logging.warning("Skipping unreadable %s", path)
            continue

        if raw.get("fetch_failed"):
            continue

        payload = raw.get("xhr_payload") or {}
        url = raw.get("listing_url", "")
        try:
            row = parse_listing(payload, url)
            rows.append(row)
        except Exception as e:
            logging.warning("Parse failed for %s: %s", path.name, e)

    if not rows:
        logging.warning("No rows to write")
        return 0

    df = pd.DataFrame(rows)
    # Ensure all schema columns exist
    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[CSV_COLUMNS]
    df.to_csv(output_csv, index=False)
    return len(df)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape TradeMe NZ Nissan Leaf listings and build leafs.csv"
    )
    parser.add_argument("--max-listings", type=int, default=None, help="Cap total detail-page fetches")
    parser.add_argument("--max-pages", type=int, default=None, help="Cap search-discovery pages")
    parser.add_argument("--refresh", action="store_true", help="Ignore cache, re-fetch every listing")
    parser.add_argument("--refresh-older-than", type=int, default=None, metavar="DAYS",
                        help="Re-fetch listings whose cache is older than DAYS days")
    parser.add_argument("--cache-dir", type=Path, default=Path("./cache"))
    parser.add_argument("--output-csv", type=Path, default=Path("./leafs.csv"))
    parser.add_argument("--headless", action="store_true", help="Run browser headless (default: headed)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Walk pagination, list IDs we'd fetch, don't open detail pages")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="Skip scraping, just rebuild CSV from existing cache")
    args = parser.parse_args(argv)

    setup_logging(args.cache_dir)
    log = logging.getLogger("fetch_leafs")

    if not args.skip_scrape:
        scraper = LeafScraper(
            cache_dir=args.cache_dir,
            headless=args.headless,
            max_listings=args.max_listings,
            max_pages=args.max_pages,
            refresh=args.refresh,
            refresh_older_than_days=args.refresh_older_than,
            dry_run=args.dry_run,
        )
        try:
            scraper.run()
        except ScrapeAbort as e:
            log.error("Scrape aborted: %s", e)
            log.info("Building CSV from whatever was captured before abort")
        except KeyboardInterrupt:
            log.warning("Interrupted by user — building CSV from cache so far")

    if args.dry_run:
        log.info("Dry run — skipping CSV build")
        return 0

    n = build_csv(args.cache_dir / "listings", args.output_csv)
    log.info("Wrote %d rows to %s", n, args.output_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
