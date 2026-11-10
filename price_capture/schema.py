"""Output schema shared by the scraper and the Domo uploader.

The Domo import is positional (no header row), so this order MUST match the
column order of the Domo dataset.
"""

CSV_COLUMNS = [
    "ean",
    "desc_sku",
    "store",
    "status",
    "price_brl",
    "found_name",
    "fetched_at",
    "message",
]
