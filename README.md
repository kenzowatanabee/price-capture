# price-capture

Collects shelf prices for a list of products (by EAN) from Brazilian pharmacy e-commerce sites and appends them to a Domo dataset.

```
data/products.csv ─┐
                   ├─> scraper.py ──> results.csv / results.json ──> upload_to_domo.py ──> Domo (APPEND)
data/stores.csv  ──┘
```

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt            # add -r requirements-dev.txt to run the tests
```

## Running

```bash
python scraper.py          # writes results.csv and results.json
python upload_to_domo.py   # appends results.csv to the Domo dataset
```

### Environment variables

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `PRODUCTS_FILE` | scraper | `data/products.csv` | Product list |
| `STORES_FILE` | scraper | `data/stores.csv` | Store list |
| `MAX_WORKERS` | scraper | `10` | Concurrent requests |
| `MIN_SUCCESS_RATE` | scraper | `0.2` | The run exits non-zero (so nothing is uploaded) if fewer than this share of requests return a price. Catches a silently broken parser |
| `MAX_ERROR_RATE` | scraper | `0.5` | ...or if more than this share are `Error` (blocked, timeouts, parse failures). `Not Found` does not count as an error |
| `VERIFY_SSL` | scraper | `true` | Set to `false` only to work around a store with a broken certificate |
| `client_id` / `DOMO_CLIENT_ID` | uploader | | Domo API client id |
| `secret_id` / `DOMO_CLIENT_SECRET` | uploader | | Domo API secret |
| `dataset_id` / `DOMO_DATASET_ID` | uploader | | Target Domo dataset |

Locally, the uploader also reads these from a `.env` file (git-ignored).

## Code layout

```
scraper.py                  entry point (python scraper.py)
upload_to_domo.py           Domo uploader
price_capture/
  runner.py                 task matrix, threading, output files, success-rate gate
  scrape.py                 one (store, product) request
  http.py                   fetch with browser impersonation, timeouts, retries
  config.py                 CSV loaders
  schema.py                 output column order (shared with the uploader)
  platforms/                one class per store architecture + registry
tests/                      unit tests (mocked HTTP)
```

## Inputs

**`data/products.csv`** has the columns `ean,desc_sku`. Spaces and dashes in the EAN are stripped.

**`data/stores.csv`** has the columns `store_id,type,domain,enabled`. Set `enabled` to `false` to skip a store. The `type` selects the scraping strategy:

| `type` | Platform | Method |
|---|---|---|
| `RD_NEXTJS` | Drogasil / Droga Raia (Next.js) | Parses `__NEXT_DATA__` from the search page |
| `VTEX_IO_HTML` | VTEX IO storefront | Parses the embedded `__STATE__` from the search page |
| `CARREFOUR_INTELLIGENT_SEARCH` | VTEX Intelligent Search | JSON API |
| `B2C_REST` | Legacy VTEX | `catalog_system` JSON API |

To add a store, append a row with an existing `type`. To support a new platform, add a class in `price_capture/platforms/` (implement `build_url` and `parse`) and register it in `price_capture/platforms/__init__.py`.

## Output and the Domo dataset

Columns, in order (defined once in `schema.py`):

`ean, desc_sku, store, status, price_brl, found_name, fetched_at, message`

- The Domo import is **positional (no header row)**, so this order must match the Domo dataset. The uploader refuses to upload if `results.csv` doesn't match `schema.py`.
- The upload uses **APPEND**. Every run adds rows, so running the workflow twice in a day (for example a manual dispatch on top of the schedule) duplicates that day's rows. Deduplicate downstream on `(ean, store, date(fetched_at))` if that matters.
- `fetched_at` is in **Brasília time** (`America/Sao_Paulo`), formatted `YYYY-MM-DD HH:MM:SS`.
- **Every attempted request produces a row**, including failures. `status` is one of `Success`, `Not Found` or `Error`, and `price_brl` is empty unless it is `Success`. Filter on `status = 'Success'` for price analysis. The other rows are useful for tracking store coverage and blocking.

## Automation

`.github/workflows/scraper.yml` runs the scraper and the Domo upload daily at 15:00 UTC (12:00 Brasília) and on manual dispatch. If the success rate falls below `MIN_SUCCESS_RATE`, the scraper step fails and the upload is skipped. `results.csv` and `results.json` are kept as a build artifact for 7 days.

Required repository secrets: `DOMO_CLIENT_ID`, `DOMO_CLIENT_SECRET`, `DOMO_DATASET_ID`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests use inline fixtures and mocked HTTP, so they need no network access.
