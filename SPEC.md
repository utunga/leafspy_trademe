# Leafspy — NZ Nissan Leaf Market Analysis

## 1. Goal & Use Case

Build a personal-use tool to help the owner decide an asking price for a **damaged 2016 Nissan Leaf 30G (30kWh) with a shot battery, otherwise roadworthy**.

The tool captures the current NZ TradeMe Nissan Leaf market, classifies listings along orthogonal axes (battery condition × body condition × trim × battery kWh), and presents an interactive dashboard for the seller to locate their car within the market.

Primary analytical question: *"What price should I list this car at, given the actual mix of comparable damaged/parts Leafs currently on the market?"*

## 2. Data Acquisition Approach

### Decision: Browser-driven scrape (not API)

The TradeMe public API requires an approved developer app, which has a multi-day approval queue and is not viable for a one-off personal task. Path chosen: **headed Playwright browser, low-volume scrape, one-off use only.**

**Acknowledged constraints:**
- TradeMe `robots.txt` explicitly disallows `/a/*listing/` and `/a/*page=`. This project crawls those patterns anyway, justified by personal, one-off, low-volume use to price the owner's own car.
- This is *not* a sustainable or sharable architecture. It exists to answer one question, once.

### Pacing (designed to be invisible)

| Step | Delay |
|---|---|
| Between detail page loads | `random.uniform(1, 20)` seconds |
| Between search result pages | `random.uniform(1, 10)` seconds |
| After page load, before extraction | `random.uniform(1, 5)` seconds |
| On HTTP 429 / 503 / CAPTCHA / login wall | **Abort the run.** No retry-and-continue. |
| Max retries per listing | 1, then mark `fetch_failed`, continue |

Expected total runtime for ~300 listings: ~50 minutes typical. Run unattended overnight.

### Browser session

- Playwright sync API, Chromium engine, **headed** (`headless=False`).
- Persistent browser context at `cache/chromium_profile/` so cookies/state survive across runs.
- **No TradeMe login** — public data only, no account association.
- No User-Agent spoofing, default viewport, default everything. Polite by design, not evading detection.
- First-run consent/region dialogs handled manually by the user — they'll see the window.

### Extraction strategy (in priority order)

1. **Capture XHR responses** the SPA makes to TradeMe's internal API via `page.on("response", ...)`. Most stable.
2. **Fall back to `__NEXT_DATA__` JSON** embedded in page source if XHR capture fails.
3. **Never parse the rendered DOM** with CSS/XPath selectors — too brittle across TradeMe re-skins.

## 3. Scrape Scope

### Search filters

- **Make/model:** Nissan / Leaf (via URL: `https://www.trademe.co.nz/a/motors/cars/nissan/leaf?page=N`)
- **Region:** all of NZ
- **Listing status:** active only (not closed/sold — TradeMe hides sold prices anyway)
- **Condition:** include wrecked / damaged / parts listings — these are the **primary** comparable set for the use case
- **Listing type:** all (BuyNow, auctions, classifieds — captured with `price_type` tag)
- **Year/odometer:** no filter at scrape time, filter in analysis

### Pagination

Walk `?page=N` URLs sequentially. Stop when:
- A page returns zero listings, OR
- Listing IDs match the previous page (defensive against TradeMe wrapping), OR
- We hit `TotalCount` if exposed in XHR response

Log a warning if pagination reaches page 50 (unexpected volume).

Sort order: default ("Featured / Best Match") — does not affect coverage.

## 4. Persistence Model

Per-listing JSON cache, with CSV as a derived artifact:

```
leafspy/
├── cache/
│   ├── chromium_profile/       # Playwright persistent context
│   ├── search_manifest.json    # discovered listing IDs + card data (incremental)
│   ├── listings/
│   │   ├── 5234567890.json     # one file per listing detail page
│   │   └── ...
│   ├── scrape.log              # full timestamped log
│   └── scrape.log.errors       # errors only
├── leafs.csv                   # derived from cache/listings/
└── leafs_summary.md            # closest-comparables markdown
```

**Properties:**
- **Idempotent**: re-run skips any listing ID already in `cache/listings/`.
- **Resumable**: search manifest written incrementally per page.
- **Re-derivable**: tweak parsing/classification, re-build CSV without re-scraping.
- `cache/` is git-ignored. CSV and notebook outputs are git-tracked artifacts.

## 5. Captured Fields (per listing JSON)

**Raw fields stored verbatim:**
- `listing_id`, `listing_url`, `fetched_at` (ISO timestamp)
- `title` (verbatim)
- `description` (full raw text, line breaks preserved)
- `buy_now_price`, `start_price`, `current_bid`, `classifieds_price` (any can be null)
- `is_negotiable` (bool — "price by negotiation" / "make an offer")
- `year`, `odometer` (int, parsed)
- `region`, `location_suburb`
- `seller_type` (private / dealer)
- `listing_type` (BuyNow / auction / classified)
- TradeMe structured motor attributes if present (transmission, fuel, body, color, *condition* field)
- `model_variant_raw` (raw token from title, e.g., "30G", "X 24kWh")
- `raw_xhr_response` (whole captured JSON, insurance against missing fields)

## 6. Derived Fields (in analysis layer)

All classification rules live in `leafspy/classify.py` so notebook and summary script share one source of truth.

| Field | Values | Source |
|---|---|---|
| `battery_kwh` | 24 / 30 / 40 / 62 / unknown | Explicit in title preferred; year-inferred fallback (pre-2013→24, 2013–2015→24, 2016–2017→30, 2018–2019→40, e+→62) |
| `battery_kwh_source` | `explicit_in_title` / `inferred_from_year` | Quality flag |
| `trim_grade` | S / X / G / SV / SL / e+ / unknown | Regex on title + description |
| `battery_status` | working / degraded / failed / unknown | Description keywords + SOH if known |
| `body_status` | roadworthy / cosmetic_damage / structural_damage / written_off / unknown | Description + title keywords |
| `damage_type` | crash / flood / fire / battery_failure / mechanical / cosmetic / none / unknown | Description keywords |
| `parts_already_stripped` | bool | "stripped", "no battery", "missing parts" keywords |
| `soh_percent` | float or null | Three-source extraction (see below) |
| `soh_bars` | int or null | Same |
| `soh_source` | `structured` / `regex_percent` / `regex_bars` / `none` | Quality flag |
| `effective_price` | float or null | `buy_now_price` ?? `classifieds_price` ?? `current_bid` (if > start_price) ?? `start_price` |
| `price_type` | which field `effective_price` came from | |
| `price_confidence` | `asking` / `auction_active` / `auction_unstarted` / `negotiable` | |

### SOH extraction priority

1. TradeMe structured `BatteryHealth` / `BatteryCondition` field if present
2. Regex on title + description for `SOH NN%`, `NN% SOH`, `battery health NN%`
3. Regex for `NN/12 bars`, `NN bars` (mapped to approximate %: 12→100, 11→85, 10→78, 9→70, …)
4. Else `soh_source='none'`

Listings without SOH stay in the dataset but are excluded from SOH-vs-price plots.

## 7. Analysis & Visualization

**Notebook:** `analyze_leafs.ipynb` — interactive Jupyter dashboard.

**Stack:** Jupyter Lab + Plotly Express + ipywidgets.

### Dashboard structure

| Cell | Content |
|---|---|
| 1 | Imports, load `leafs.csv` via `leafspy/schema.py` |
| 2 | **Shared filter widgets** — multi-select for trim_grade, battery_kwh, battery_status, body_status, price_type; range slider for year. Drive all plots below. |
| 3 | Dropdown for x-axis selection (year / odometer / soh_percent) |
| 4 | **2D scatter:** x = chosen axis, y = effective_price, color = trim_grade. Hover tooltips: title, region, listing_url (clickable markdown link), description excerpt. |
| 5 | **3D scatter:** x = year, y = soh_percent, z = effective_price, color = trim_grade. Drag-to-rotate. |
| 6 | **Box plot:** effective_price by battery_status × body_status 2×2 cell — surfaces the "battery donor" and "shell car" market segments. |
| 7 | **Box plot:** effective_price by trim_grade, faceted by battery_status |
| 8 | **Markdown summary** (rendered from `leafspy/summarize.py`) — closest comparables to seller's car spec, with median/range/dealer-vs-private skew |

The seller's own car is **not** plotted as a marker — they can read where it sits.

### Markdown summary spec (`summarize.py` output)

For comparables filter (configurable, defaults: same trim_grade, same battery_status, same body_status, year ±2):
- Count of matches
- Median, 25th–75th percentile range
- Dealer vs private skew
- Adjacent-cell context: working-version median, donor-car median, implied "shell value"

## 8. Project Layout

```
leafspy/
├── fetch_leafs.py              # scraper CLI entry point
├── analyze_leafs.ipynb         # interactive dashboard
├── leafspy/                    # shared package
│   ├── __init__.py
│   ├── scrape.py               # Playwright orchestration
│   ├── parse.py                # extract structured fields from XHR/page
│   ├── classify.py             # all classification rules (battery_status, body_status, SOH, variant, etc.)
│   ├── schema.py               # CSV columns, dtypes, comparable-set defaults
│   └── summarize.py            # markdown comparables summary
├── cache/                      # gitignored
├── leafs.csv                   # derived
├── leafs_summary.md            # derived
├── pyproject.toml
├── .gitignore                  # excludes cache/, .env, __pycache__/
└── README.md
```

## 9. Dev Environment

- **Python 3.11+**
- **uv** for venv + dependency management

**Dependencies (`pyproject.toml`):**
- `playwright` — browser automation
- `beautifulsoup4` — description text cleanup
- `pandas` — dataframe / CSV
- `plotly` — interactive plots
- `jupyterlab` — notebook environment
- `ipywidgets` — filter UI in notebook
- `tqdm` — scrape progress bar

**Setup:**
```
uv sync
uv run playwright install chromium
```

**Run:**
```
uv run python fetch_leafs.py                  # full scrape
uv run python fetch_leafs.py --max-listings 10  # smoke test
uv run jupyter lab analyze_leafs.ipynb        # open dashboard
```

## 10. CLI Surface (`fetch_leafs.py`)

| Flag | Default | Purpose |
|---|---|---|
| `--max-listings N` | unlimited | Cap detail-page fetches (smoke testing) |
| `--max-pages N` | unlimited | Cap search-discovery pages (smoke testing) |
| `--refresh` | off | Ignore cache, re-fetch everything |
| `--refresh-older-than D` | off | Re-fetch listings whose cache file is >D days old |
| `--cache-dir PATH` | `./cache` | Override cache location |
| `--output-csv PATH` | `./leafs.csv` | Override CSV output path |
| `--headless` | off (headed) | Opt-in headless mode |
| `--dry-run` | off | Walk pagination, list IDs we'd fetch, don't open detail pages |

## 11. Logging

| Channel | Content |
|---|---|
| Stdout | `tqdm` progress bar; per-listing line `[N/M] {id} "{title}" → cached (Xs)` |
| `cache/scrape.log` | Timestamped record of every fetch, retry, error, sleep — append mode, survives across runs |
| `cache/scrape.log.errors` | Errors only, easier to grep |

Standard Python `logging`, single `basicConfig` call. No `structlog` / `loguru` — overkill for a one-off.

## 12. Known Limitations

- **One-off design.** Not built for repeated/scheduled runs. If TradeMe re-skins their listing pages or changes XHR shape, the parser breaks and needs manual fixing.
- **ToS-grey by design.** This is acceptable for personal one-off use; do not adapt for any other context.
- **SOH parsing is heuristic.** Listings that report SOH only via embedded Leaf Spy screenshots are not OCR'd — those listings will have `soh_source='none'`.
- **No sold-price data.** TradeMe doesn't expose final sale prices; the analysis is based entirely on *asking* prices.
- **`battery_kwh` inferred from year for ~half of listings** (sellers don't always state battery size). Tagged via `battery_kwh_source` so analysis can filter to explicit-only when desired.
- **Damage/condition classification is keyword-based and imperfect.** Cache the full description text so rules can be refined post-scrape without re-fetching.
