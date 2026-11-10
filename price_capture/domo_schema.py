"""Helpers to compare and plan the Domo dataset schema against the CSV schema."""
import os

from price_capture.schema import COLUMNS


class SchemaError(Exception):
    """The Domo schema cannot be reconciled with the CSV schema automatically."""


def read_credentials(env=None):
    """(client_id, client_secret, dataset_id) from the environment; entries are None when missing."""
    env = os.environ if env is None else env
    return (
        env.get("client_id") or env.get("DOMO_CLIENT_ID"),
        env.get("secret_id") or env.get("DOMO_CLIENT_SECRET"),
        env.get("dataset_id") or env.get("DOMO_DATASET_ID"),
    )


def columns_from_dataset(dataset):
    """[(name, type), ...] from the dict returned by `client.datasets.get()`."""
    return [(c["name"], c["type"]) for c in (dataset.get("schema") or {}).get("columns", [])]


def schema_mismatch(domo_column_count, csv_column_count=None):
    """
    Message describing why an import would misalign, or None if the column counts agree.
    The import is positional, so the counts (and order) must match.
    """
    csv_column_count = len(COLUMNS) if csv_column_count is None else csv_column_count
    if domo_column_count == csv_column_count:
        return None
    return (
        f"The Domo dataset has {domo_column_count} columns but the CSV has {csv_column_count} columns. "
        "Run `python update_domo_schema.py` (dry run first, then --apply) to update the dataset schema."
    )


def plan_schema(current, canonical=None, rename_existing=False):
    """
    Plans the new Domo schema. Returns (desired, notes).

    Default: keep every existing column exactly as it is (name and type, so existing data and
    dashboards are untouched) and append the missing canonical columns at the end.
    rename_existing=True: replace the whole schema with the canonical names and types.
    Raises SchemaError if the dataset has more columns than the canonical schema.
    """
    canonical = list(COLUMNS if canonical is None else canonical)
    current = [tuple(c) for c in current]

    if len(current) > len(canonical):
        raise SchemaError(
            f"The Domo dataset has {len(current)} columns, more than the {len(canonical)} the scraper "
            "writes. Refusing to drop columns automatically; fix the dataset in Domo first."
        )

    notes = []
    for i, (name, dtype) in enumerate(current):
        want_name, want_type = canonical[i]
        if name.lower() != want_name.lower():
            notes.append(
                f"column {i + 1}: Domo has '{name}' but the scraper writes '{want_name}' there. "
                "The import is positional, so check this column holds the right data."
            )
        if dtype != want_type:
            notes.append(f"column {i + 1} ('{name}'): type is {dtype} in Domo, scraper type is {want_type}.")

    if rename_existing:
        return canonical, notes
    return current + canonical[len(current):], notes
