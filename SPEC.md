# Leafspy — NZ Nissan Leaf Market Analysis

## 1. Goal & Use Case

Build a personal-use tool to help the owner decide an asking price for a **damaged 2016 Nissan Leaf 30G (30kWh) with a shot battery, otherwise roadworthy**.

The tool captures the current NZ TradeMe Nissan Leaf market, classifies listings along orthogonal axes (battery condition × body condition × trim × battery kWh), and presents an interactive dashboard for the seller to locate their car within the market.

Primary analytical question: *"What price should I list this car at, given the actual mix of comparable damaged/parts Leafs currently on the market?"*

## 2. Data Acquisition Approach

### Decision: Tampermonkey userscript captures the user's real Firefox browsing (not browser automation, not API).

History: an earlier design used Playwright to drive a headed Chromium browser. TradeMe's bot detection flagged Playwright's Chromium even with a persistent profile, so the design pivoted to a passive-capture model.

**The user is the crawler.** They browse TradeMe in real Firefox at human pace. A Tampermonkey userscript silently captures `__NEXT_DATA__` and any XHR/fetch responses the page makes, and POSTs each capture to a local Python receiver. No bot detection because the browser is genuinely real.

### Components

| Component | Role |
|---|---|
| `leaf_harvester.user.js` | Tampermonkey userscript. Runs on `*.trademe.co.nz/*`. Monkey-patches `window.fetch` and `XMLHttpRequest` at `document-start`. Reads `__NEXT_DATA__` at DOMContentLoaded. POSTs each capture to `http://localhost:8765/capture` via `GM_xmlhttpRequest`. Shows a corner-of-screen capture-count badge for user feedback. |
| `receiver.py` | Local `ThreadingHTTPServer` on 127.0.0.1:8765. Routes captures: listing-detail URLs (`/listing/{id}`) → merge into `cache/listings/{id}.json`; search URLs (`/a/motors/cars/...`) → accumulate listings into `cache/search_manifest.json`; everything → backup copy in `cache/raw_captures/`. |
| `fetch_leafs.py` | Reads `cache/listings/*.json`, applies `leafspy/parse.py`, writes `leafs.csv`. No network. |
| `leafspy/parse.py` | Converts a raw TradeMe XHR/__NEXT_DATA__ payload into a flat CSV row. Field names are speculative — iterate after first real capture. |
| `leafspy/classify.py` | Pure-function classifiers (battery_status, body_status, damage_type, SOH, variant). |
| `analyze_leafs.ipynb` | Interactive Plotly/ipywidgets dashboard, shared filters across all panels. |

### Pacing & politeness

There is no pacing logic in the system. **You provide the pacing by browsing at human speed.** TradeMe sees a normal Firefox session at normal-human rates. No retry-with-backoff, no jitter, no robot.

### Acknowledged constraint

TradeMe `robots.txt` disallows `/a/*listing/` and `/a/*page=`. The chosen architecture sidesteps this by being indistinguishable from the user manually viewing those pages — they are manually viewing them, the userscript only observes what the browser already loaded. Personal one-off use only; this is not a sustainable or sharable design.

## 3. Capture Scope

### What gets captured

Every page on `*.trademe.co.nz` triggers the userscript. For each page:
- All JSON-content-type XHR/fetch responses (200 status only)
- The `__NEXT_DATA__` blob from the page source
- The current `page_url`, `captured_at`, and `source` (`fetch` / `xhr` / `next_data`)

### What gets persisted

The receiver:
- Always saves the raw capture to `cache/raw_captures/{ts}_{source}.json`
- If `page_url` matches `/listing/(\d+)`, merges into `cache/listings/{id}.json`
- If `page_url` matches `/a/motors/cars/`, extracts listings array and updates `cache/search_manifest.json`

The user controls coverage by what they choose to browse. Practically: visit each listing detail page once. The userscript handles the rest silently.

## 4. Persistence Model

```
leafspy/
├── cache/
│   ├── raw_captures/
│   │   └── {ts}_{source}.json   # every capture, backup/debug
│   ├── listings/
│   │   ├── 5234567890.json      # merged per-listing capture
│   │   └── ...
│   ├── search_manifest.json     # accumulated card data from search pages
│   ├── search_captures/         # reserved for future use
│   ├── receiver.log
│   └── build.log
├── leafs.csv                    # derived from cache/listings/
└── leafs_summary.md             # derived
```

**Per-listing JSON shape** (`cache/listings/{id}.json`):

```json
{
  "listing_id": 5234567890,
  "listing_url": "https://www.trademe.co.nz/.../listing/5234567890",
  "captured_at": 1747000000.0,
  "xhr_payload": { /* listing-shaped dict, picked from captures */ },
  "all_captures": [ /* every capture event for this listing */ ]
}
```

Properties:
- **Idempotent**: re-browsing a listing appends to `all_captures`, refreshes `xhr_payload`.
- **Re-derivable**: tweak parsing/classification rules, re-run `fetch_leafs.py`, no re-browsing needed.
- `cache/` is gitignored. CSV and notebook are derived artifacts.

## 5. Captured Fields (raw, per listing JSON)

Stored verbatim from the captured payload:
- `listing_id`, `listing_url`, `captured_at`
- `title`, `description` (free text — full body)
- `buy_now_price`, `start_price`, `current_bid`, `classifieds_price` (any may be null)
- `is_negotiable` (bool — "price by negotiation" / "make an offer")
- `year`, `odometer`
- `region`, `location_suburb`
- `seller_type` (private / dealer)
- `listing_type` (BuyNow / auction / classified)
- `model_variant_raw` (raw token from title, e.g., "30G", "X 24kWh")
- Whatever else TradeMe surfaces in the payload (the full dict is retained in `xhr_payload`)

## 6. Derived Fields (in analysis layer)

All classification rules live in `leafspy/classify.py` so notebook and summary script share one source of truth.

| Field | Values | Source |
|---|---|---|
| `battery_kwh` | 24 / 30 / 40 / 62 / unknown | Explicit in title preferred; year-inferred fallback |
| `battery_kwh_source` | `explicit_in_title` / `inferred_from_year` / `unknown` | Quality flag |
| `trim_grade` | S / X / G / SV / SL / e+ / unknown | Regex on title + description |
| `battery_status` | working / degraded / failed / unknown | SOH if known; else description keywords |
| `body_status` | roadworthy / cosmetic_damage / structural_damage / written_off / unknown | Description + title keywords |
| `damage_type` | crash / flood / fire / battery_failure / mechanical / cosmetic / none / unknown | Description keywords |
| `parts_already_stripped` | bool | "stripped", "no battery", "missing parts" keywords |
| `soh_percent` | float or null | Three-source extraction |
| `soh_bars` | int or null | Same |
| `soh_source` | `structured` / `regex_percent` / `regex_bars` / `none` | Quality flag |
| `effective_price` | float or null | `buy_now_price` ?? `classifieds_price` ?? `current_bid` (if > start_price) ?? `start_price` |
| `price_type` | which field `effective_price` came from | |
| `price_confidence` | `asking` / `auction_active` / `auction_unstarted` / `negotiable` | |

### SOH extraction priority

1. TradeMe structured field if present in payload
2. Regex `SOH NN%`, `NN% SOH`, `battery health NN%`
3. Regex `NN/12 bars`, `NN bars`, mapped to approximate % (12→100, 11→85, …)
4. Else `soh_source='none'`

## 7. Analysis & Visualization

**Notebook:** `analyze_leafs.ipynb` — interactive Jupyter dashboard.

**Stack:** Jupyter Lab + Plotly Express + ipywidgets.

### Dashboard structure

| Cell | Content |
|---|---|
| 1 | Imports, load `leafs.csv` |
| 2 | Shared filter widgets — multi-select for trim_grade, battery_kwh, battery_status, body_status, price_type; range slider for year. |
| 3 | X-axis dropdown (year / odometer / soh_percent) |
| 4 | 2D scatter — x = chosen axis, y = effective_price, color = trim_grade. Hover: title, region, listing_url, key fields. |
| 5 | 3D scatter — year × soh_percent × effective_price, color = trim_grade. |
| 6 | Box plot — effective_price by battery_status × body_status 2×2 cell. |
| 7 | Box plot — effective_price by trim_grade, faceted by battery_status |
| 8 | Markdown summary — closest comparables to the seller's car spec. |

## 8. Project Layout

```
leafspy/
├── leaf_harvester.user.js       # Tampermonkey userscript
├── receiver.py                  # local HTTP capture receiver
├── fetch_leafs.py               # cache → CSV
├── analyze_leafs.ipynb          # interactive dashboard
├── leafspy/                     # shared package
│   ├── __init__.py
│   ├── parse.py                 # payload → schema row
│   ├── classify.py              # battery/body/SOH/variant heuristics
│   ├── schema.py                # CSV columns, enums
│   └── summarize.py             # markdown comparables summary
├── cache/                       # gitignored
├── leafs.csv                    # derived
├── leafs_summary.md             # derived
├── pyproject.toml
├── .gitignore
├── SPEC.md
└── README.md
```

## 9. Dev Environment

- **Python 3.11+**
- **uv** for venv + dependency management
- **Firefox** (only browser tested)
- **Tampermonkey** Firefox extension

**Dependencies (`pyproject.toml`):** `pandas`, `plotly`, `jupyterlab`, `ipywidgets`, `anywidget`. No playwright, no scraping libs — receiver uses stdlib only.

## 10. CLI Surface

### `receiver.py`

```
receiver.py
  --cache-dir PATH      # default ./cache
  --port N              # default 8765
  --host HOST           # default 127.0.0.1
```

### `fetch_leafs.py`

```
fetch_leafs.py
  --cache-dir PATH      # default ./cache
  --output-csv PATH     # default ./leafs.csv
```

## 11. Logging

| Channel | Content |
|---|---|
| Stdout (receiver) | Per-capture line: `capture: xhr https://...trademe.co.nz/.../listing/12345 → listing/12345` |
| `cache/receiver.log` | Persistent receiver log, append mode |
| `cache/build.log` | `fetch_leafs.py` parse warnings / counts |

## 12. Known Limitations

- **Coverage = what you browse.** If you don't visit a listing in Firefox, it won't be in the dataset. Practically: open the search results, page through, click into each listing of interest.
- **`leafspy/parse.py` field names are speculative.** First captures will reveal what TradeMe actually uses. Inspect a `cache/listings/{id}.json` `xhr_payload`, update the `_KEY_*` lists, re-run.
- **One-off design.** Not built for repeated/scheduled runs. The userscript will keep working until TradeMe restructures its frontend.
- **SOH parsing is heuristic.** Listings with SOH only in image screenshots are not OCR'd.
- **No sold-price data.** Asking prices only.
- **ToS-grey by design.** Personal one-off use to price the owner's car. Not adaptable to other contexts.

## 13. Historical Note

A previous design used Playwright to drive a headed Chromium with persistent profile and jittered pacing. TradeMe's bot detection flagged Playwright's CDP-controlled Chromium even at very low rates, so the design pivoted to the userscript model. The non-acquisition code (parse, classify, schema, summarize, notebook) was unchanged across the pivot — only the data-acquisition layer was replaced.
