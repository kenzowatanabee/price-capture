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
| `MAX_WORKERS` | scraper | `10` | Max requests in flight across all stores |
| `MIN_SUCCESS_RATE` | scraper | `0.2` | The run exits non-zero (so nothing is uploaded) if fewer than this share of requests return a price. Catches a silently broken parser |
| `MAX_ERROR_RATE` | scraper | `0.5` | ...or if more than this share are `Error` (blocked, timeouts, parse failures). `Not Found` does not count as an error |
| `VERIFY_SSL` | scraper | `true` | Global kill switch: `false` disables TLS verification for **every** store. Prefer the per-store `verify_ssl` column in `stores.csv` |
| `PER_STORE_CONCURRENCY` | scraper | `2` | Max simultaneous requests to the same store. Each store runs in its own small pool, so a slow store cannot hold up the others |
| `STORE_FAILURE_LIMIT` | scraper | `5` | After this many consecutive timeouts or network errors from one store, its remaining requests are skipped (`SKIPPED`). `0` disables this |
| `REQUEST_JITTER` | scraper | `0.3` | Random delay (0 to N seconds) before each request |
| `IMPERSONATE` | scraper | `chrome142` | Browser fingerprint curl_cffi impersonates; its User-Agent and `sec-ch-ua` headers come with it |
| `RUN_ID` | scraper | | Overrides the generated `run_id` (default: `gh-<run>-<attempt>` on GitHub Actions, else `local-<timestamp>`) |
| `client_id` / `DOMO_CLIENT_ID` | uploader | | Domo API client id |
| `secret_id` / `DOMO_CLIENT_SECRET` | uploader | | Domo API secret |
| `dataset_id` / `DOMO_DATASET_ID` | uploader | | Target Domo dataset |

Locally, the uploader and `update_domo_schema.py` also read the Domo variables from a `.env` file (git-ignored). Copy `.env.example` to `.env` and fill it in.

## Code layout

```
scraper.py                  entry point (python scraper.py)
upload_to_domo.py           Domo uploader (refuses to run if the dataset schema does not match)
update_domo_schema.py       local script that updates the Domo dataset schema (see below)
price_capture/
  runner.py                 task matrix, threading, output files, success-rate gate
  scrape.py                 one (store, product) request
  http.py                   sessions, browser impersonation, per-store limits, retries (429/503, timeouts, connection resets)
  results.py                result dicts with reason codes
  config.py                 CSV loaders
  schema.py                 output columns, order and Domo types (single source of truth)
  domo_schema.py            compares/plans the Domo schema against schema.py
  platforms/                one class per store architecture + registry
tests/                      unit tests (mocked HTTP)
```

## Inputs

**`data/products.csv`** has the columns `ean,desc_sku`. Spaces and dashes in the EAN are stripped.

**`data/stores.csv`** has the columns `store_id,type,domain,enabled,verify_ssl`. Set `enabled` to `false` to skip a store. `verify_ssl` is optional (default `true`); set it to `false` for a single store whose certificate chain cannot be verified (the run log announces every store that has it off). The `type` selects the scraping strategy:

| `type` | Platform | Method |
|---|---|---|
| `RD_NEXTJS` | Drogasil / Droga Raia (Next.js) | Parses `__NEXT_DATA__` from the search page |
| `VTEX_IO_HTML` | VTEX IO storefront | Parses the embedded `__STATE__` from the search page |
| `CARREFOUR_INTELLIGENT_SEARCH` | VTEX Intelligent Search | JSON API |
| `B2C_REST` | Legacy VTEX | `catalog_system` JSON API |

To add a store, append a row with an existing `type`. To support a new platform, add a class in `price_capture/platforms/` (implement `build_url` and `parse`) and register it in `price_capture/platforms/__init__.py`.

## Output and the Domo dataset

Columns, in order (defined once in `price_capture/schema.py`):

| # | Column | Domo type | Notes |
|---|---|---|---|
| 1 | `ean` | STRING | |
| 2 | `desc_sku` | STRING | From `products.csv` |
| 3 | `store` | STRING | |
| 4 | `status` | STRING | `Success`, `Not Found` or `Error` |
| 5 | `price_brl` | DECIMAL | Selling price. Only set when `status = Success` |
| 6 | `found_name` | STRING | Product name as the store lists it |
| 7 | `fetched_at` | DATETIME | UTC, `YYYY-MM-DD HH:MM:SS`. Domo converts it to the company time zone for display |
| 8 | `message` | STRING | Reason code and detail for non-successes |
| 9 | `list_price` | DOUBLE | Store's list ("from") price, when reported |
| 10 | `available` | STRING | `true` / `false`, empty when the store does not say |
| 11 | `seller` | STRING | Seller name |
| 12 | `url` | STRING | Product page |
| 13 | `run_id` | STRING | Same value for every row of one scraper run |
| 14 | `fetched_at_brt` | STRING | Brasília clock time of the same moment, as text, so Domo shows it unchanged |

Columns 9 to 12 are filled for `B2C_REST` and `CARREFOUR_INTELLIGENT_SEARCH` stores. They are empty for `RD_NEXTJS` and `VTEX_IO_HTML` stores, because those payload fields have not been mapped yet. They are also filled on `OUT_OF_STOCK` and `ZERO_PRICE` rows, since they describe the product, while `price_brl` stays empty.

- The Domo import is **positional (no header row)**, so this order must match the Domo dataset. The uploader refuses to upload if `results.csv` doesn't match `schema.py`, or if the Domo dataset has a different number of columns.
- The upload uses **APPEND**. Every run adds rows, so running the workflow twice in a day (for example a manual dispatch on top of the schedule) duplicates that day's rows. Deduplicate downstream on `(ean, store, date(fetched_at))` if that matters.
- **Time zones:** `fetched_at` is the real moment in **UTC**. Domo treats DATETIME values as UTC and displays them in the company time zone, so it is not shown as Brasília time. Use `fetched_at` for sorting and time maths, and `fetched_at_brt` (text, Brasília time) when you want to see the local clock time.
- **Every attempted request produces a row**, including failures. `status` is one of `Success`, `Not Found` or `Error`, and `price_brl` is empty unless it is `Success`.

  `message` starts with a reason code (`CODE: detail`) for every non-success:

  | `status` | Codes |
  |---|---|
  | `Not Found` | `NOT_LISTED` (store returned no product), `OUT_OF_STOCK`, `ZERO_PRICE` (store reports a price of 0), `NO_PRICE`, `NO_STATE` (page had no embedded product data, which may mean the layout changed) |
  | `Error` | `TIMEOUT`, `NETWORK`, `HTTP_ERROR` (e.g. `HTTP_ERROR: 403`), `BLOCKED`, `PARSE_ERROR`, `CONFIG`, `SKIPPED` (store unreachable: see `STORE_FAILURE_LIMIT`) |

  Filter on `status = 'Success'` for price analysis. The other rows are useful for tracking store coverage and blocking.

### Updating the Domo dataset schema

When columns are added to `schema.py`, the Domo dataset must be updated **before** the next upload; until then the uploader stops with an error and sends nothing.

```bash
cp .env.example .env                        # fill in DOMO_CLIENT_ID, DOMO_CLIENT_SECRET, DOMO_DATASET_ID
python update_domo_schema.py                # dry run: shows current vs proposed schema
python update_domo_schema.py --apply        # saves a backup of the dataset definition, updates, then re-reads to verify
```

By default existing columns keep their names and types (so existing data and dashboards are untouched) and the missing ones are appended. `--rename-existing` replaces the whole schema with the one in `schema.py`. The script never drops columns, and refuses a dataset that has more columns than the scraper writes.

## Regional pricing check

The daily scraper sends no location, so it records each store's **default online price**. `region_check.py` is a separate local analysis that checks whether a store prices or stocks differently by CEP. For each VTEX (`B2C_REST`) store it asks which seller serves a CEP (`/api/checkout/pub/regions`) and simulates a cart there (`/api/checkout/pub/orderForms/simulation`).

```bash
python region_check.py                                   # 3 single-pack SKUs x default CEPs x all B2C_REST stores
python region_check.py --limit 5 --stores "Drogal,Pague Menos"
python region_check.py --eans 7891024132005 --ceps "SP=01310-100,PE=50030-230"
```

It writes `region_check.csv` (git-ignored) with one row per store, SKU and CEP. A `catalog` row shows the price the scraper records, for comparison. It then prints per store how many SKUs had more than one price across CEPs. The default CEPs are city centres in SP, RJ, MG, PR, RS, BA, PE and DF; pass your own with `--ceps`. RD stores are not covered yet.

## Automation

`.github/workflows/scraper.yml` runs the test suite, then the scraper and the Domo upload, daily at 15:00 UTC (12:00 Brasília) and on manual dispatch. If the run looks broken (error rate above `MAX_ERROR_RATE`, or success rate below `MIN_SUCCESS_RATE`), the scraper step fails and the upload is skipped. A store with no successful prices at all shows up as a warning annotation on the run. `results.csv` and `results.json` are kept as a build artifact for 7 days.

Required repository secrets: `DOMO_CLIENT_ID`, `DOMO_CLIENT_SECRET`, `DOMO_DATASET_ID`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests use inline fixtures and mocked HTTP, so they need no network access.
