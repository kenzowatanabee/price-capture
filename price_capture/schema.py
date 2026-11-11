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
]

CSV_COLUMNS = [name for name, _ in COLUMNS]
