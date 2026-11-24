"""Output schema shared by the scraper, the Domo uploader and the Domo schema script.

The Domo import is positional (no header row), so CSV_COLUMNS MUST match the column order of
the Domo dataset. New columns are only ever appended at the end.
"""

# (column name, Domo column type), in CSV / Domo order. Types mirror the production dataset.
COLUMNS = [
    ("ean", "STRING"),
    ("desc_sku", "STRING"),
    ("store", "STRING"),
    ("status", "STRING"),
    ("price_brl", "DECIMAL"),
    ("found_name", "STRING"),
    ("fetched_at", "DATETIME"),  # UTC "YYYY-MM-DD HH:MM:SS"; Domo converts it to the company time zone
    ("message", "STRING"),
    ("list_price", "DOUBLE"),  # list ("from") price reported by the store, when present
    ("available", "STRING"),  # "true" / "false", empty when the store does not say
    ("seller", "STRING"),
    ("url", "STRING"),  # product page, when the store payload carries it
    ("run_id", "STRING"),  # identifies the scraper run that produced the row
    ("fetched_at_brt", "STRING"),  # Brasilia clock time as text, shown by Domo exactly as written
    ("image_url", "STRING"),  # main product picture on the store's own site, when the payload carries it
    ("seller_id", "STRING"),  # the store's id for the seller of this offer
    ("seller_type", "STRING"),  # "store" = sold by the store itself, "partner" = a marketplace seller on its site
    # A store and product can have several rows in one run: the main one ("true": the offer that is
    # compared and counted) plus one per other seller on the store's site ("false"). Rows written
    # before this column existed are empty and are all main rows.
    ("main_offer", "STRING"),
    ("seller_cnpj", "STRING"),  # the seller's company registration, when the store publishes it
]

CSV_COLUMNS = [name for name, _ in COLUMNS]

