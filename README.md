# Leafspy — NZ Nissan Leaf Market Analysis

Personal-use tool for pricing a used Nissan Leaf on TradeMe (NZ), with a focus on damaged / parts / battery-shot listings.

See [`SPEC.md`](./SPEC.md) for the full design.

## Setup

Requires Python 3.11+ and `uv`.

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Python deps
uv sync

# Install Chromium for Playwright
uv run playwright install chromium
```

## Usage

### 1. Smoke test (recommended first run)

Exercise the whole pipeline against ~10 listings to verify TradeMe's response shape matches `leafspy/parse.py`'s assumptions:

```bash
uv run python fetch_leafs.py --max-listings 10
```

This will:
1. Open a headed Chromium window — handle any cookie/region dialogs interactively
2. Walk the first search page, capture XHR responses with listing data
3. Visit up to 10 listing detail pages, save raw JSON to `cache/listings/`
4. Build `leafs.csv` from the cached data

**After the smoke test, inspect `cache/listings/*.json`** to confirm TradeMe's field names match what `leafspy/parse.py` expects. The `_KEY_*` lists at the top of `parse.py` are speculative and likely need adjustment. The cached raw XHR payload lets you tune the parser and re-derive the CSV without re-scraping:

```bash
uv run python fetch_leafs.py --skip-scrape  # rebuild CSV from cache
```

### 2. Full overnight scrape

```bash
uv run python fetch_leafs.py
```

Expected runtime: ~50min for ~300 listings (rough). Will abort on HTTP 429/503, CAPTCHA, or login redirect.

### 3. Open the dashboard

```bash
uv run jupyter lab analyze_leafs.ipynb
```

Run all cells. The filter widgets at the top drive all plots simultaneously.

Edit the last cell to match your car's actual spec:

```python
my_car = SellerCar(
    trim_grade='G',
    battery_kwh=30,
    year=2016,
    battery_status='failed',
    body_status='roadworthy',
)
```

## CLI flags

| Flag | Default | Purpose |
|---|---|---|
| `--max-listings N` | unlimited | Cap detail-page fetches |
| `--max-pages N` | unlimited | Cap search-discovery pages |
| `--refresh` | off | Re-fetch every listing (ignore cache) |
| `--refresh-older-than DAYS` | off | Re-fetch listings cached >N days ago |
| `--cache-dir PATH` | `./cache` | |
| `--output-csv PATH` | `./leafs.csv` | |
| `--headless` | off (headed) | |
| `--dry-run` | off | Walk pagination, don't open detail pages |
| `--skip-scrape` | off | Rebuild CSV from existing cache only |

## Project layout

```
leafspy/
├── fetch_leafs.py              # scraper CLI entry point
├── analyze_leafs.ipynb         # interactive dashboard
├── leafspy/                    # shared package
│   ├── scrape.py               # Playwright orchestration
│   ├── parse.py                # XHR → structured fields (SPECULATIVE — verify after first run)
│   ├── classify.py             # battery/body/SOH/variant heuristics
│   ├── schema.py               # CSV columns, dtypes
│   └── summarize.py            # closest-comparables markdown
├── cache/                      # gitignored — raw scrape data
│   ├── chromium_profile/       # persistent browser context
│   ├── search_manifest.json
│   ├── listings/{id}.json      # raw XHR captures per listing
│   └── scrape.log
├── leafs.csv                   # derived artifact
└── SPEC.md                     # full design doc
```

## Known limitations

- **TradeMe's actual XHR shape is unverified at first build.** Expect to refine `leafspy/parse.py` after the smoke test reveals real field names. The per-listing JSON cache makes this iterative — no re-scraping needed.
- **SOH parsing is heuristic.** Listings reporting SOH only via embedded Leaf Spy screenshots are not OCR'd — they'll have `soh_source='none'`.
- **No sold-price data.** TradeMe doesn't expose final sale prices. The analysis is based entirely on asking prices.
- **One-off design.** Not built for repeated/scheduled runs. If TradeMe re-skins pages or changes XHR shape, the parser needs manual fixing.
- **ToS-grey by design** — see SPEC.md §2 for the deliberate choice. Personal one-off use only.

## Troubleshooting

**Scrape aborts immediately with "CAPTCHA detected" or "login wall":** TradeMe has flagged you. Stop, wait 24h, and consider longer delays (edit `_sleep_detail` in `scrape.py`).

**Smoke test runs but `leafs.csv` is empty / all null:** The parser key candidates didn't match real field names. Inspect `cache/listings/*.json`, find the actual keys in `xhr_payload`, and update the `_KEY_*` lists in `leafspy/parse.py`.

**Discovery walks 50 pages but finds nothing:** The XHR endpoint sift heuristic in `scrape.py:_find_listings_in_responses` didn't catch TradeMe's actual response format. Inspect the captured JSONs (you can add a `print(captured_jsons)` line to debug) and update the key shapes searched.
