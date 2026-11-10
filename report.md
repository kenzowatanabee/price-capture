Scraping complete. Total requests processed: 750

Per-store success:
  Droga Raia            : 45/75
  Drogal                : 71/75
  Drogaria Catarinense  : 64/75
  Drogaria São Paulo    : 74/75
  Drogaria Venancio     : 75/75
  Drogarias Pacheco     : 0/75
  ⚠️ Drogarias Pacheco: no successful prices (75 requests). Blocked or layout changed?
  Drogasil              : 48/75
  Farmácia Indiana      : 74/75
  Farmácias São João    : 0/75
  ⚠️ Farmácias São João: no successful prices (75 requests). Blocked or layout changed?
  Pague Menos           : 69/75

Non-success reasons:
  NETWORK: 150
  NO_PRICE: 57
  NOT_LISTED: 15
  OUT_OF_STOCK: 8
Overall: 520/750 (69%)

Dataset: PRICE_TRACKER (#), rows: 53563

  #  current                      proposed
  1  ean (STRING)                 ean (STRING)
  2  desc_sku (STRING)            desc_sku (STRING)
  3  store (STRING)               store (STRING)
  4  status (STRING)              status (STRING)
  5  price_brl (DECIMAL)          price_brl (DECIMAL)
  6  found_name (STRING)          found_name (STRING)
  7  fetched_at (DATETIME)        fetched_at (DATETIME)
  8  message (STRING)             message (STRING)
  9  list_price (DOUBLE)          list_price (DOUBLE)
 10  available (STRING)           available (STRING)
 11  seller (STRING)              seller (STRING)
 12  url (STRING)                 url (STRING)
 13  run_id (STRING)              run_id (STRING)
⚠️  column 5 ('price_brl'): type is DECIMAL in Domo, scraper type is DOUBLE.
⚠️  column 7 ('fetched_at'): type is DATETIME in Domo, scraper type is STRING.

✅ The schema is already up to date. Nothing to do.