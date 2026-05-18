#!/usr/bin/env python3
"""Build leafs.csv from harvested cache.

The Tampermonkey userscript + receiver.py do the actual data acquisition
(you browse TradeMe in Firefox; captures land in cache/listings/). This
script just parses the cache into a clean CSV.

Re-run any time after refining leafspy/parse.py or classify.py — no
re-browsing required.
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


def setup_logging(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(cache_dir / "build.log"),
        ],
    )


def build_csv(listings_dir: Path, output_csv: Path) -> int:
    rows = []
    skipped_no_payload = 0
    parse_failures = 0

    for path in sorted(listings_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError:
            logging.warning("Skipping unreadable %s", path.name)
            continue

        payload = raw.get("xhr_payload")
        if payload is None:
            skipped_no_payload += 1
            continue

        url = raw.get("listing_url", "")
        try:
            row = parse_listing(payload, url)
            rows.append(row)
        except Exception as e:
            parse_failures += 1
            logging.warning("Parse failed for %s: %s", path.name, e)

    if skipped_no_payload:
        logging.info("Skipped %d listings with no xhr_payload yet", skipped_no_payload)
    if parse_failures:
        logging.warning("Parse failures: %d (see warnings above)", parse_failures)

    if not rows:
        logging.warning("No rows to write — is cache/listings/ populated?")
        return 0

    df = pd.DataFrame(rows)
    for col in CSV_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[CSV_COLUMNS]
    df.to_csv(output_csv, index=False)
    return len(df)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build leafs.csv from harvested cache.")
    parser.add_argument("--cache-dir", type=Path, default=Path("./cache"))
    parser.add_argument("--output-csv", type=Path, default=Path("./leafs.csv"))
    args = parser.parse_args(argv)

    setup_logging(args.cache_dir)
    log = logging.getLogger("fetch_leafs")

    listings_dir = args.cache_dir / "listings"
    if not listings_dir.exists():
        log.error("No %s directory found. Run receiver.py and browse TradeMe first.", listings_dir)
        return 1

    n = build_csv(listings_dir, args.output_csv)
    log.info("Wrote %d rows to %s", n, args.output_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
