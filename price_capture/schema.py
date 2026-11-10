"""Output schema shared by the scraper, the Domo uploader and the Domo schema script.

The Domo import is positional (no header row), so CSV_COLUMNS MUST match the column order of
the Domo dataset. New columns are only ever appended at the end.
"""

# (column name, Domo column type), in CSV / Domo order.
COLUMNS = [
    ("ean", "STRING"),
    ("desc_sku", "STRING"),
    ("store", "STRING"),
    ("status", "STRING"),
    ("price_brl", "DOUBLE"),
    ("found_name", "STRING"),
    ("fetched_at", "STRING"),  # "YYYY-MM-DD HH:MM:SS" in Brasilia time
    ("message", "STRING"),
    ("list_price", "DOUBLE"),  # list ("from") price reported by the store, when present
    ("available", "STRING"),  # "true" / "false", empty when the store does not say
    ("seller", "STRING"),
    ("url", "STRING"),  # product page, when the store payload carries it
    ("run_id", "STRING"),  # identifies the scraper run that produced the row
]

CSV_COLUMNS = [name for name, _ in COLUMNS]
