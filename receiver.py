#!/usr/bin/env python3
"""Local HTTP receiver for the Leaf Harvester userscript.

Run alongside your browsing session:

    python receiver.py

The Tampermonkey userscript POSTs each captured page/XHR payload to
http://localhost:8765/capture. The receiver:

  - Always saves a raw copy to cache/raw_captures/ (for debugging & re-ingest)
  - If the page URL matches a listing detail (.../listing/{id}), merges the
    capture into cache/listings/{id}.json
  - If the page is a search results page, accumulates listing card data into
    cache/search_manifest.json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional


# Will be set in main()
CACHE_DIR: Path = Path("./cache")
LISTINGS_DIR: Path = CACHE_DIR / "listings"
RAW_DIR: Path = CACHE_DIR / "raw_captures"
SEARCH_DIR: Path = CACHE_DIR / "search_captures"
MANIFEST: Path = CACHE_DIR / "search_manifest.json"

LISTING_URL_RE = re.compile(r"/listing/(\d+)")
SEARCH_URL_RE = re.compile(r"/a/motors/cars/", re.IGNORECASE)

# Keys that suggest a payload is a TradeMe listing-shaped dict.
TITLE_KEYS = ("Title", "title")
ID_KEYS = ("ListingId", "listingId", "Id", "id")
PRICE_KEYS = (
    "BuyNowPrice", "buyNowPrice", "StartPrice", "startPrice",
    "PriceDisplay", "priceDisplay", "AskingPrice",
)


def looks_like_listing(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    has_title = any(k in d for k in TITLE_KEYS)
    has_id = any(k in d for k in ID_KEYS)
    has_price = any(k in d for k in PRICE_KEYS)
    return has_title and (has_id or has_price)


def find_listing_in_tree(node: Any) -> Optional[dict]:
    """Walk a possibly-deeply-nested JSON tree, return first listing-shaped dict."""
    if looks_like_listing(node):
        return node
    if isinstance(node, dict):
        for v in node.values():
            found = find_listing_in_tree(v)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_listing_in_tree(item)
            if found is not None:
                return found
    return None


def find_listings_array(node: Any) -> Optional[list]:
    """Find the most likely listings array inside a search payload tree."""
    best: Optional[list] = None
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, list) and v and looks_like_listing(v[0]):
                if best is None or len(v) > len(best):
                    best = v
            else:
                found = find_listings_array(v)
                if found is not None and (best is None or len(found) > len(best)):
                    best = found
    elif isinstance(node, list):
        for item in node:
            found = find_listings_array(item)
            if found is not None and (best is None or len(found) > len(best)):
                best = found
    return best


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def save_raw_capture(body: dict) -> None:
    ts = int(time.time() * 1000)
    source = body.get("source", "unknown")
    path = RAW_DIR / f"{ts}_{source}.json"
    path.write_text(json.dumps(body, default=str))


def merge_into_listing(listing_id: int, capture: dict) -> None:
    path = LISTINGS_DIR / f"{listing_id}.json"
    existing: dict
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    existing.setdefault("listing_id", listing_id)
    existing.setdefault("all_captures", [])
    existing["listing_url"] = capture.get("page_url") or existing.get("listing_url")
    existing["captured_at"] = capture.get("captured_at") or time.time()

    existing["all_captures"].append(capture)

    # If we don't yet have a listing-shaped xhr_payload, try to derive one.
    payload = capture.get("payload")
    if existing.get("xhr_payload") is None:
        if looks_like_listing(payload):
            existing["xhr_payload"] = payload
        else:
            extracted = find_listing_in_tree(payload)
            if extracted is not None:
                existing["xhr_payload"] = extracted

    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, default=str, indent=2))
    tmp.replace(path)


def update_search_manifest(capture: dict) -> int:
    """Extract listings from a search-page capture, merge into manifest. Returns count added."""
    payload = capture.get("payload")
    listings = find_listings_array(payload)
    if not listings:
        return 0

    manifest: dict
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text())
        except json.JSONDecodeError:
            manifest = {}
    else:
        manifest = {}

    added = 0
    for card in listings:
        for k in ID_KEYS:
            if k in card and card[k] is not None:
                key = str(card[k])
                if key not in manifest:
                    added += 1
                manifest[key] = {
                    "listing_id": card[k],
                    "card_data": card,
                    "discovered_from": capture.get("page_url"),
                }
                break

    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, default=str, indent=2))
    tmp.replace(MANIFEST)
    return added


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        # Permissive — runs on localhost only.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}\n')
            return
        self.send_response(404)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/capture":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            logging.warning("Bad request body: %s", e)
            self.send_response(400)
            self._cors()
            self.end_headers()
            return

        page_url = body.get("page_url", "")
        source = body.get("source", "unknown")

        try:
            save_raw_capture(body)
        except Exception as e:
            logging.error("Failed to save raw capture: %s", e)

        action = "raw_only"
        m = LISTING_URL_RE.search(page_url or "")
        if m:
            lid = int(m.group(1))
            try:
                merge_into_listing(lid, body)
                action = f"listing/{lid}"
            except Exception as e:
                logging.error("Failed to merge listing %s: %s", lid, e)
        elif SEARCH_URL_RE.search(page_url or ""):
            try:
                added = update_search_manifest(body)
                action = f"search (+{added} new)"
            except Exception as e:
                logging.error("Failed to update search manifest: %s", e)

        logging.info("capture: %s %s → %s", source, page_url[:90], action)

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}\n')

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Suppress default access-log spam — we log meaningful events ourselves.
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    global CACHE_DIR, LISTINGS_DIR, RAW_DIR, SEARCH_DIR, MANIFEST

    parser = argparse.ArgumentParser(description="Local capture receiver for the Leaf Harvester userscript.")
    parser.add_argument("--cache-dir", type=Path, default=Path("./cache"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)

    CACHE_DIR = args.cache_dir
    LISTINGS_DIR = CACHE_DIR / "listings"
    RAW_DIR = CACHE_DIR / "raw_captures"
    SEARCH_DIR = CACHE_DIR / "search_captures"
    MANIFEST = CACHE_DIR / "search_manifest.json"

    for d in (CACHE_DIR, LISTINGS_DIR, RAW_DIR, SEARCH_DIR):
        d.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(CACHE_DIR / "receiver.log"),
        ],
    )
    log = logging.getLogger("receiver")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    log.info("Leaf Harvester receiver listening on http://%s:%d", args.host, args.port)
    log.info("Cache dir: %s", CACHE_DIR.resolve())
    log.info("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopping receiver")
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
