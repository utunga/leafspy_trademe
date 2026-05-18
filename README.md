# Leafspy — NZ Nissan Leaf Market Analysis

Personal-use tool for pricing a used Nissan Leaf on TradeMe (NZ), with a focus on damaged / parts / battery-shot listings.

Data is **harvested from your own real Firefox** as you browse TradeMe naturally — a Tampermonkey userscript silently captures listing data and POSTs it to a local Python receiver. No browser automation, no bot detection, no scraping infrastructure on your machine.

See [`SPEC.md`](./SPEC.md) for the full design.

## How it works

```
   Firefox + Tampermonkey  ──capture──▶  receiver.py  ──writes──▶  cache/listings/{id}.json
                                                                          │
                                                                          ▼
                                                                   fetch_leafs.py
                                                                          │
                                                                          ▼
                                                                      leafs.csv
                                                                          │
                                                                          ▼
                                                             analyze_leafs.ipynb
```

## Setup (one-time)

### 1. Install Python deps

Requires Python 3.11+ and `uv`.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # if you don't have uv
uv sync
```

### 2. Install Tampermonkey in Firefox

[addons.mozilla.org → Tampermonkey](https://addons.mozilla.org/en-US/firefox/addon/tampermonkey/). Click "Add to Firefox", accept permissions.

### 3. Install the harvester userscript

Open `leaf_harvester.user.js` in your editor. Open the Tampermonkey dashboard (click the extension icon → Dashboard), click the **+** tab to create a new script, paste the file contents, save (Ctrl+S). Confirm it's enabled.

## Usage (each session)

### 1. Start the receiver

```bash
uv run python receiver.py
```

You should see:
```
Leaf Harvester receiver listening on http://127.0.0.1:8765
Cache dir: /Users/chur/dev/leafspy/cache
Ctrl+C to stop.
```

Leave it running.

### 2. Browse TradeMe in Firefox

Visit https://www.trademe.co.nz/a/motors/cars/nissan/leaf and browse normally — page through search results, click into listings you want to capture. You'll see a small "Leafspy: N" badge in the bottom-right corner of every TradeMe page showing how many captures the script has sent.

- **Green:** all good, receiver is responding
- **Orange:** receiver returned an error
- **Red:** receiver is offline — start it (`python receiver.py`)

The receiver writes:
- `cache/raw_captures/*.json` — every capture, as backup
- `cache/listings/{id}.json` — merged per-listing data (parsed downstream)
- `cache/search_manifest.json` — accumulated listing card data from search pages
- `cache/receiver.log` — what's happening

Browse through every listing you want analyzed. The script is silent — no scrape pacing needed because *you* are the pacing.

### 3. Build the CSV

After browsing, stop the receiver (`Ctrl+C`), then:

```bash
uv run python fetch_leafs.py
```

This reads `cache/listings/*.json` and writes `leafs.csv`.

### 4. Open the dashboard

```bash
uv run jupyter lab analyze_leafs.ipynb
```

Run all cells. Filter widgets at the top drive every plot.

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

## Iterating on parse rules

The parser (`leafspy/parse.py`) has speculative TradeMe field-name guesses. After your first browsing session:

1. Inspect a sample of `cache/listings/{id}.json` — look at `xhr_payload` for the real key names
2. Update `_KEY_*` lists at the top of `leafspy/parse.py`
3. Re-run `uv run python fetch_leafs.py` — no re-browsing needed

The cache is the source of truth; CSV is just a derived view.

## Project layout

```
leafspy/
├── leaf_harvester.user.js       # Tampermonkey userscript (install once in Firefox)
├── receiver.py                  # local HTTP capture receiver
├── fetch_leafs.py               # cache → CSV
├── analyze_leafs.ipynb          # interactive dashboard
├── leafspy/                     # shared package
│   ├── parse.py                 # XHR payload → structured fields
│   ├── classify.py              # battery/body/SOH/variant heuristics
│   ├── schema.py                # CSV columns, enums
│   └── summarize.py             # closest-comparables markdown
├── cache/                       # gitignored — all harvested data
│   ├── listings/{id}.json
│   ├── raw_captures/
│   ├── search_manifest.json
│   └── receiver.log
├── leafs.csv                    # derived artifact
└── SPEC.md                      # design doc
```

## Known limitations

- **`leafspy/parse.py` field names are speculative.** Inspect a real capture, update the `_KEY_*` lists, re-run `fetch_leafs.py`.
- **SOH parsing is heuristic.** Listings reporting SOH only via embedded Leaf Spy screenshots are not OCR'd — they'll have `soh_source='none'`.
- **No sold-price data.** Asking prices only.
- **You are the crawler.** Tool only captures pages you actually visit in Firefox. If you want comprehensive coverage of all Leafs on the market, you need to actually browse them all.
- **Personal use only.** This tool is built for one specific job (pricing the owner's car). Not intended as ongoing infrastructure.

## Troubleshooting

**Badge stays red ("offline"):** `receiver.py` isn't running, or it's running on a different port. Restart with `uv run python receiver.py`.

**Badge is green but `cache/listings/` stays empty:** TradeMe URL doesn't match the listing-detail regex (`/listing/(\d+)`). Check `cache/raw_captures/` to see what page_urls are being captured, and update `LISTING_URL_RE` in `receiver.py` if needed.

**`fetch_leafs.py` runs but `leafs.csv` is mostly nulls:** Parser key-name guesses don't match TradeMe's actual response shape. Inspect `cache/listings/*.json` → `xhr_payload` to find the real key names, update `leafspy/parse.py`.

**Tampermonkey says "userscript blocked":** Make sure Tampermonkey has permission to run on TradeMe domains. Check the script's matches: `https://www.trademe.co.nz/*` and `https://trademe.co.nz/*` must be allowed.
