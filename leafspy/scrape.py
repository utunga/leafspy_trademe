"""Playwright orchestration: discovery + detail capture.

Run with `python fetch_leafs.py` (not this module directly).

Behaviour:
- Headed Chromium with persistent context at cache/chromium_profile/.
- Discovery: walk /a/motors/cars/nissan/leaf?page=N, capture XHR responses
  containing listings array, write to cache/search_manifest.json incrementally.
- Detail: for each listing URL, visit page, capture XHR responses, write
  raw to cache/listings/{id}.json.
- Jittered pacing per SPEC §2.
- Abort on first sign of trouble (429/503/captcha/login redirect).
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from playwright.sync_api import (
    BrowserContext,
    Page,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

logger = logging.getLogger(__name__)


SEARCH_BASE = "https://www.trademe.co.nz/a/motors/cars/nissan/leaf"


class ScrapeAbort(Exception):
    """Raised when the scraper detects a state it should not push past."""


class LeafScraper:
    def __init__(
        self,
        cache_dir: Path,
        headless: bool = False,
        max_listings: Optional[int] = None,
        max_pages: Optional[int] = None,
        refresh: bool = False,
        refresh_older_than_days: Optional[int] = None,
        dry_run: bool = False,
    ):
        self.cache_dir = Path(cache_dir)
        self.profile_dir = self.cache_dir / "chromium_profile"
        self.listings_dir = self.cache_dir / "listings"
        self.manifest_path = self.cache_dir / "search_manifest.json"

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.listings_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        self.headless = headless
        self.max_listings = max_listings
        self.max_pages = max_pages
        self.refresh = refresh
        self.refresh_older_than_days = refresh_older_than_days
        self.dry_run = dry_run

        self.manifest: dict[str, dict] = {}
        self._load_manifest()

    # ---- manifest persistence ----
    def _load_manifest(self) -> None:
        if self.manifest_path.exists():
            try:
                self.manifest = json.loads(self.manifest_path.read_text())
                logger.info("Loaded manifest with %d listings", len(self.manifest))
            except json.JSONDecodeError:
                logger.warning("Manifest unreadable, starting fresh")
                self.manifest = {}

    def _save_manifest(self) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.manifest, indent=2, default=str))
        tmp.replace(self.manifest_path)

    # ---- pacing ----
    @staticmethod
    def _sleep_detail() -> None:
        d = random.uniform(1, 20)
        logger.debug("Sleep %.2fs (detail)", d)
        time.sleep(d)

    @staticmethod
    def _sleep_search() -> None:
        d = random.uniform(1, 10)
        logger.debug("Sleep %.2fs (search)", d)
        time.sleep(d)

    @staticmethod
    def _sleep_post_load() -> None:
        d = random.uniform(1, 5)
        logger.debug("Sleep %.2fs (post-load)", d)
        time.sleep(d)

    # ---- danger checks ----
    @staticmethod
    def _check_danger(page: Page, response_log: list[dict]) -> None:
        """Raise ScrapeAbort if we see a soft block, captcha, or login wall."""
        url = page.url
        if "login" in url.lower() or "signin" in url.lower():
            raise ScrapeAbort(f"Redirected to login wall: {url}")

        content = page.content().lower()
        if "captcha" in content or "are you a robot" in content:
            raise ScrapeAbort(f"CAPTCHA detected on {url}")

        for entry in response_log[-20:]:  # only recent
            status = entry.get("status")
            if status in (429, 503):
                raise ScrapeAbort(f"HTTP {status} from {entry.get('url')}")

    # ---- discovery ----
    def discover(self, context: BrowserContext) -> list[str]:
        """Walk search pages, capture listing URLs into manifest."""
        page = context.new_page()
        page_num = 1
        prev_page_ids: set[str] = set()

        while True:
            if self.max_pages and page_num > self.max_pages:
                logger.info("Hit --max-pages cap at %d", self.max_pages)
                break
            if page_num > 50:
                logger.warning("Pagination reached page 50 — capping defensively")
                break

            url = f"{SEARCH_BASE}?page={page_num}"
            logger.info("Discovery page %d: %s", page_num, url)

            response_log: list[dict] = []
            captured_jsons: list[dict] = []

            def on_response(response: Response):
                try:
                    response_log.append({"url": response.url, "status": response.status})
                    ct = (response.headers or {}).get("content-type", "")
                    if "application/json" in ct and response.status == 200:
                        try:
                            captured_jsons.append({"url": response.url, "body": response.json()})
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug("Response handler error: %s", e)

            page.on("response", on_response)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                # Wait for the listing data to load (network settles).
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeoutError:
                    logger.debug("networkidle timeout — proceeding anyway")
                self._sleep_post_load()
                self._check_danger(page, response_log)
            finally:
                page.remove_listener("response", on_response)

            listings = self._find_listings_in_responses(captured_jsons)
            if not listings:
                logger.info("No listings on page %d — discovery complete", page_num)
                break

            page_ids = {str(self._extract_listing_id(li)) for li in listings if self._extract_listing_id(li)}
            if page_ids and page_ids == prev_page_ids:
                logger.warning("Page %d duplicates page %d — stopping", page_num, page_num - 1)
                break
            prev_page_ids = page_ids

            new_count = 0
            for li in listings:
                lid = self._extract_listing_id(li)
                if not lid:
                    continue
                key = str(lid)
                if key not in self.manifest:
                    new_count += 1
                self.manifest[key] = {
                    "listing_id": lid,
                    "card_data": li,
                    "discovered_on_page": page_num,
                }
            self._save_manifest()
            logger.info("Page %d: %d listings (%d new)", page_num, len(listings), new_count)

            if self.max_listings and len(self.manifest) >= self.max_listings:
                logger.info("Hit --max-listings cap at %d", self.max_listings)
                break

            page_num += 1
            self._sleep_search()

        page.close()
        return list(self.manifest.keys())

    @staticmethod
    def _find_listings_in_responses(captured: list[dict]) -> list[dict]:
        """Sift captured XHR JSON bodies for a listings array.

        TradeMe's frontend likely returns a payload with a 'List' or 'Listings'
        or 'data.list' field containing the cards. We try several shapes.
        """
        candidates = []
        for entry in captured:
            body = entry.get("body")
            if not isinstance(body, dict):
                continue
            # Try common shapes
            for key in ("List", "Listings", "list", "listings", "items", "results"):
                if isinstance(body.get(key), list) and body[key]:
                    candidates.append(body[key])
                    break
            else:
                # Nested under data/.list etc.
                data = body.get("data") if isinstance(body.get("data"), dict) else None
                if data:
                    for key in ("list", "items", "results", "listings"):
                        if isinstance(data.get(key), list) and data[key]:
                            candidates.append(data[key])
                            break
        # Pick the largest list (most likely the search results)
        if not candidates:
            return []
        return max(candidates, key=len)

    @staticmethod
    def _extract_listing_id(card: dict) -> Optional[int]:
        for key in ("ListingId", "listingId", "Id", "id"):
            v = card.get(key)
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.isdigit():
                return int(v)
        return None

    # ---- detail fetch ----
    def fetch_details(self, context: BrowserContext) -> None:
        page = context.new_page()
        ids_to_fetch = self._select_ids_to_fetch()
        logger.info("Detail phase: %d listings to fetch", len(ids_to_fetch))

        for i, lid in enumerate(ids_to_fetch, 1):
            cache_file = self.listings_dir / f"{lid}.json"
            url = self._listing_url_from_manifest(lid)
            if url is None:
                logger.warning("[%d/%d] %s: no URL in manifest, skipping", i, len(ids_to_fetch), lid)
                continue

            if self.dry_run:
                logger.info("[%d/%d] DRY-RUN would fetch %s", i, len(ids_to_fetch), url)
                continue

            t0 = time.time()
            try:
                data = self._fetch_one_listing(page, lid, url)
                cache_file.write_text(json.dumps(data, indent=2, default=str))
                elapsed = time.time() - t0
                logger.info(
                    "[%d/%d] %s \"%s\" → cached (%.1fs)",
                    i, len(ids_to_fetch), lid, data.get("title", "")[:60], elapsed,
                )
            except ScrapeAbort:
                raise
            except Exception as e:
                logger.error("[%d/%d] %s: fetch failed: %s — retrying once", i, len(ids_to_fetch), lid, e)
                time.sleep(5)
                try:
                    data = self._fetch_one_listing(page, lid, url)
                    cache_file.write_text(json.dumps(data, indent=2, default=str))
                    logger.info("[%d/%d] %s: retry succeeded", i, len(ids_to_fetch), lid)
                except Exception as e2:
                    logger.error("[%d/%d] %s: retry failed: %s — marking and continuing", i, len(ids_to_fetch), lid, e2)
                    cache_file.write_text(json.dumps({"listing_id": int(lid), "fetch_failed": True, "error": str(e2)}))

            self._sleep_detail()

        page.close()

    def _select_ids_to_fetch(self) -> list[str]:
        ids = list(self.manifest.keys())
        if self.max_listings:
            ids = ids[: self.max_listings]
        if self.refresh:
            return ids
        # Otherwise, skip ones already cached and recent enough
        result = []
        cutoff = time.time() - (self.refresh_older_than_days * 86400) if self.refresh_older_than_days else None
        for lid in ids:
            cache_file = self.listings_dir / f"{lid}.json"
            if not cache_file.exists():
                result.append(lid)
                continue
            if cutoff is not None and cache_file.stat().st_mtime < cutoff:
                result.append(lid)
                continue
        return result

    def _listing_url_from_manifest(self, lid: str) -> Optional[str]:
        entry = self.manifest.get(lid)
        if not entry:
            return None
        card = entry.get("card_data") or {}
        for key in ("ListingUrl", "Url", "url", "DetailPageUrl"):
            v = card.get(key)
            if v:
                return urljoin("https://www.trademe.co.nz", v)
        # Fall back to constructed URL (TradeMe pattern)
        return f"https://www.trademe.co.nz/a/motors/cars/nissan/leaf/listing/{lid}"

    def _fetch_one_listing(self, page: Page, lid: str, url: str) -> dict:
        response_log: list[dict] = []
        captured_jsons: list[dict] = []

        def on_response(response: Response):
            try:
                response_log.append({"url": response.url, "status": response.status})
                ct = (response.headers or {}).get("content-type", "")
                if "application/json" in ct and response.status == 200:
                    try:
                        captured_jsons.append({"url": response.url, "body": response.json()})
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("Response handler error: %s", e)

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PlaywrightTimeoutError:
                pass
            self._sleep_post_load()
            self._check_danger(page, response_log)

            html = page.content()
        finally:
            page.remove_listener("response", on_response)

        # Find the listing-shaped payload among captured XHRs.
        listing_payload = None
        for entry in captured_jsons:
            body = entry.get("body")
            if isinstance(body, dict) and any(k in body for k in ("Title", "title")):
                # Prefer the one with most price/condition fields
                if any(k in body for k in ("BuyNowPrice", "StartPrice", "Body", "BodyHtml")):
                    listing_payload = body
                    break

        return {
            "listing_id": int(lid),
            "listing_url": url,
            "captured_at": time.time(),
            "xhr_payload": listing_payload,
            "all_xhr_urls": [e["url"] for e in captured_jsons],
            "html_excerpt": html[:50_000] if listing_payload is None else None,  # only keep HTML when XHR missed
        }

    # ---- entry point ----
    def run(self) -> None:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                viewport={"width": 1280, "height": 800},
            )
            try:
                self.discover(context)
                self.fetch_details(context)
            finally:
                context.close()
